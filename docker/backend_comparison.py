"""Run the frozen navigation-backend comparison for Phase 3 task 3.2.

The script runs on the host and drives the pinned Docker image. Each backend gets
its own worker thread, so the default and alternative backends share the host at
the same time and see comparable contention. Every container records its cgroup
peak memory and CPU time, and a sampler keeps raw ``docker stats`` rows.

Example::

    python docker/backend_comparison.py --image envfuzz:phase3-base \
        --output runs/phase3-resume/backend-comparison/frozen \
        --seed-plan runs/phase3-resume/backend-comparison/seed-plan/plan.json
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
CONTAINER_ROOT = "/workspace/envfuzz"
CONTAINER_PREFIX = "bc-"
INFRASTRUCTURE = {"watchdog", "simulator_failed", "launch_failed"}
EXAMPLES = ("scenario", "timed-move", "scripted-actor", "friction-patch")
SEEDED_CASES = {"seed42-apartment-case0": 0, "seed42-corridor-case6": 6}
BACKENDS = {
    "slam_toolbox": None,
    "amcl": "examples/amcl-localization-profile.yaml",
}
STATS_INTERVAL_S = 1.0
ENTRYPOINT = (
    'unset DISPLAY; source /opt/ros/jazzy/setup.bash && source /opt/envfuzz-sim/local_setup.bash; '
    'envfuzz "${@:2}"; rc=$?; '
    '{ echo "memory_peak_bytes $(cat /sys/fs/cgroup/memory.peak)"; cat /sys/fs/cgroup/cpu.stat; } > "$1"; '
    'exit $rc'
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def container_path(path: Path) -> str:
    return f"{CONTAINER_ROOT}/{path.resolve().relative_to(REPOSITORY).as_posix()}"


def prepare_suite(output: Path, seed_plan: Path) -> list[dict]:
    """Freeze the scenario inputs inside the run directory and hash them."""
    suite_dir = output / "suite"
    suite_dir.mkdir(parents=True)
    suite = []
    for name in EXAMPLES:
        committed = subprocess.run(["git", "show", f"HEAD:examples/{name}.yaml"], cwd=REPOSITORY,
                                   check=True, capture_output=True).stdout
        if committed != (REPOSITORY / "examples" / f"{name}.yaml").read_bytes():
            raise RuntimeError(f"examples/{name}.yaml differs from HEAD; the image would use another input")
        target = suite_dir / f"{name}.yaml"
        target.write_bytes(committed)
        suite.append({"name": name, "source": f"examples/{name}.yaml@HEAD"})
    cases = json.loads(seed_plan.read_text(encoding="utf-8"))["cases"]
    for name, index in SEEDED_CASES.items():
        target = suite_dir / f"{name}.yaml"
        target.write_text(json.dumps(cases[index]["scenario"], indent=2) + "\n", encoding="utf-8")
        suite.append({"name": name, "source": f"{seed_plan.name} cases[{index}] "
                      f"({cases[index]['layout']}, {cases[index]['method']})"})
    for entry in suite:
        path = suite_dir / f"{entry['name']}.yaml"
        entry.update(path=str(path.relative_to(REPOSITORY)), sha256=sha256(path))
    for profile in filter(None, BACKENDS.values()):
        suite.append({"name": Path(profile).stem, "path": profile, "sha256": sha256(REPOSITORY / profile)})
    return suite


def record_host(output: Path, image: str) -> None:
    def command(*args: str) -> str:
        return subprocess.run(args, cwd=REPOSITORY, capture_output=True, text=True, check=True).stdout.strip()

    host = {
        "recorded_unix_s": time.time(),
        "platform": platform.platform(),
        "python": sys.version,
        "git_head": command("git", "rev-parse", "HEAD"),
        "git_status_porcelain": command("git", "status", "--porcelain"),
        "docker_version": json.loads(command("docker", "version", "--format", "{{json .}}")),
        "docker_info": json.loads(command("docker", "info", "--format", "{{json .}}")),
        "image": image,
        "image_inspect": json.loads(command("docker", "image", "inspect", image))[0],
    }
    host["image_inspect"].pop("RootFS", None)
    (output / "host.json").write_text(json.dumps(host, indent=2) + "\n", encoding="utf-8")
    (output / "image.txt").write_text(f"{image} {host['image_inspect']['Id']}\n", encoding="utf-8")


def sample_stats(path: Path, stop: threading.Event) -> None:
    """Append raw docker stats rows for comparison containers until stopped."""
    with path.open("a", encoding="utf-8") as handle:
        while not stop.is_set():
            stamp = time.time()
            rows = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                                  capture_output=True, text=True, check=False).stdout.splitlines()
            for row in rows:
                data = json.loads(row)
                if data.get("Name", "").startswith(CONTAINER_PREFIX):
                    handle.write(json.dumps({"unix_s": stamp, **data}) + "\n")
            handle.flush()
            stop.wait(STATS_INTERVAL_S)


class Worker:
    def __init__(self, args: argparse.Namespace, backend: str, suite: list[dict]):
        self.args, self.backend, self.profile = args, backend, BACKENDS[backend]
        self.scenarios = [entry for entry in suite if entry["name"] not in
                          {Path(p).stem for p in BACKENDS.values() if p}]
        self.containers: list[dict] = []

    def docker(self, name: str, phase: str, scenario: str, envfuzz_args: list[str]) -> int:
        resources = self.args.output / "resources" / f"{name}.txt"
        console = self.args.output / "console" / f"{name}.log"
        command = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "--cpus", str(self.args.cpus), "--memory", self.args.memory, "--memory-swap", self.args.memory,
            "-v", f"{REPOSITORY / 'runs'}:{CONTAINER_ROOT}/runs",
            "--entrypoint", "/bin/bash", self.args.image, "-c", ENTRYPOINT, "entry",
            container_path(resources), *envfuzz_args,
        ]
        started = time.monotonic()
        with console.open("w", encoding="utf-8") as log:
            log.write(" ".join(command) + "\n")
            log.flush()
            returncode = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        self.containers.append({
            "container": name, "backend": self.backend, "scenario": scenario, "phase": phase,
            "returncode": returncode, "started_unix_s": time.time() - (time.monotonic() - started),
            "wall_s": time.monotonic() - started, **read_resources(resources),
        })
        return returncode

    def profile_args(self) -> list[str]:
        return ["--profile", self.profile] if self.profile else []

    def calibrate(self, scenario: dict, root: Path) -> Path | None:
        for attempt in range(1, self.args.calibration_attempts + 1):
            directory = root / f"calibration-{attempt}"
            name = f"{CONTAINER_PREFIX}{self.backend}-{scenario['name']}-cal{attempt}".replace("_", "-")
            self.docker(name, "calibration", scenario["name"], [
                "baseline", container_path(REPOSITORY / scenario["path"]), "--n", str(self.args.trials),
                "--timeout", str(self.args.timeout), "--watchdog", str(self.args.watchdog),
                "--output", container_path(directory), *self.profile_args(),
            ])
            if (directory / "baseline.json").is_file():
                return directory / "baseline.json"
        return None

    def scored_run(self, scenario: dict, root: Path, baseline: Path, repeat: int) -> None:
        for retry in range(self.args.retries + 1):
            suffix = f"run-{repeat}" + (f"-retry-{retry}" if retry else "")
            bundle = root / suffix
            name = f"{CONTAINER_PREFIX}{self.backend}-{scenario['name']}-{suffix}".replace("_", "-")
            self.docker(name, "scored", scenario["name"], [
                "run", container_path(REPOSITORY / scenario["path"]), "--baseline", container_path(baseline),
                "--timeout", str(self.args.timeout), "--watchdog", str(self.args.watchdog),
                "--output", container_path(bundle), *self.profile_args(),
            ])
            result = bundle / "result.json"
            status = json.loads(result.read_text(encoding="utf-8"))["status"] if result.is_file() else None
            if status is not None and status not in INFRASTRUCTURE:
                return

    def run(self) -> None:
        for scenario in self.scenarios:
            root = self.args.output / self.backend / scenario["name"]
            root.mkdir(parents=True)
            baseline = self.calibrate(scenario, root)
            if baseline is None:
                continue
            for repeat in range(1, self.args.repeats + 1):
                self.scored_run(scenario, root, baseline, repeat)


def read_resources(path: Path) -> dict:
    if not path.is_file():
        return {"memory_peak_bytes": None, "cpu_usage_usec": None}
    values = dict(line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {"memory_peak_bytes": int(values["memory_peak_bytes"]), "cpu_usage_usec": int(values["usage_usec"])}


def episode_row(backend: str, scenario: str, phase: str, bundle: Path) -> dict:
    result = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    metrics_path, map_path = bundle / "metrics.json", bundle / "map-metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.is_file() else {}
    maps = json.loads(map_path.read_text(encoding="utf-8")) if map_path.is_file() else {}
    resolved = json.loads((bundle / "run.json").read_text(encoding="utf-8"))["simulation_profile"]
    return {
        "backend": backend, "resolved_backend": resolved["navigation_backend"], "scenario": scenario,
        "phase": phase, "bundle": str(bundle.relative_to(REPOSITORY)), "status": result["status"],
        "classification": result["classification"], "reason": result["reason"],
        "goals_reached": metrics.get("goals_reached"), "goal_count": metrics.get("goal_count"),
        "score": result["score"], "tiers_hit": "|".join(result["tiers_hit"]),
        "event_kinds": "|".join(f"{event['tier']}:{event['kind']}" for event in result["events"]),
        "sim_duration_s": result["sim_duration"], "wall_duration_s": result["wall_duration"],
        "episode_rtf": result["real_time_factor"],
        "mission_wall_s": metrics.get("mission_wall_duration_s"),
        "mission_rtf": metrics.get("mission_real_time_factor"),
        "position_rmse_m": metrics.get("position_rmse_m"),
        "recoveries": len(metrics.get("recoveries", [])) if metrics else None,
        "map_error": maps.get("map_error"),
    }


def collect_rows(output: Path) -> list[dict]:
    rows = []
    for backend in BACKENDS:
        for bundle in sorted((output / backend).glob("*/*")):
            if bundle.name.startswith("run-") and (bundle / "result.json").is_file():
                rows.append(episode_row(backend, bundle.parent.name, "scored", bundle))
        for trial in sorted((output / backend).glob("*/calibration-*/trial-*")):
            if (trial / "result.json").is_file():
                row = episode_row(backend, trial.parents[1].name, "calibration", trial)
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def spread(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"n": len(values), "median": statistics.median(values), "min": min(values), "max": max(values)}


def numbers(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row[key] not in ("", "None")]


def peak_cpu_percent(stats: Path) -> dict[str, float]:
    peaks: dict[str, float] = {}
    if stats.is_file():
        for line in stats.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            value = float(row["CPUPerc"].rstrip("%"))
            peaks[row["Name"]] = max(value, peaks.get(row["Name"], 0.0))
    return peaks


def summarize_rows(rows: list[dict]) -> dict:
    valid = [row for row in rows if row["status"] not in INFRASTRUCTURE]
    completed = [row for row in valid if row["status"] == "completed"]
    return {
        "attempts": len(rows), "valid_episodes": len(valid),
        "infrastructure_attempts": len(rows) - len(valid),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "goals_reached": sum(int(row["goals_reached"] or 0) for row in valid),
        "goal_count": sum(int(row["goal_count"] or 0) for row in valid),
        "score_total": sum(float(row["score"]) for row in valid if row["score"] not in ("", "None")),
        "episodes_with_score": sum(1 for row in valid if row["score"] not in ("", "None", "0", "0.0")),
        "event_counts": dict(Counter(kind for row in valid for kind in row["event_kinds"].split("|") if kind)),
        "sim_duration_completed_s": spread(numbers(completed, "sim_duration_s")),
        "wall_duration_s": spread(numbers(rows, "wall_duration_s")),
        "episode_rtf": spread(numbers(valid, "episode_rtf")),
        "mission_rtf": spread(numbers(valid, "mission_rtf")),
        "position_rmse_m": spread(numbers(valid, "position_rmse_m")),
        "recoveries_total": sum(int(row["recoveries"]) for row in valid if row["recoveries"] not in ("", "None")),
    }


def summarize_containers(rows: list[dict], cpu_peaks: dict[str, float]) -> dict:
    memory = [int(row["memory_peak_bytes"]) / 2**20 for row in rows if row["memory_peak_bytes"]]
    cores = [int(row["cpu_usage_usec"]) / 1e6 / float(row["wall_s"]) for row in rows if row["cpu_usage_usec"]]
    sampled = [cpu_peaks[row["container"]] for row in rows if row["container"] in cpu_peaks]
    return {"containers": len(rows), "memory_peak_mib": spread(memory),
            "mean_cpu_cores": spread(cores), "docker_stats_peak_cpu_percent": spread(sampled),
            "container_wall_s": spread([float(row["wall_s"]) for row in rows])}


def summarize(output: Path) -> dict:
    episodes, containers = read_csv(output / "results.csv"), read_csv(output / "containers.csv")
    cpu_peaks = peak_cpu_percent(output / "docker-stats.jsonl")
    summary: dict = {"backends": {}}
    for backend in BACKENDS:
        mine = [row for row in episodes if row["backend"] == backend]
        boxes = [row for row in containers if row["backend"] == backend]
        scored = [row for row in mine if row["phase"] == "scored"]
        summary["backends"][backend] = {
            "resolved_backends": sorted({row["resolved_backend"] for row in mine}),
            "scored": summarize_rows(scored),
            "calibration": {**summarize_rows([row for row in mine if row["phase"] == "calibration"]),
                            "calibration_containers": dict(Counter(
                                f"{row['scenario']}:rc={row['returncode']}" for row in boxes
                                if row["phase"] == "calibration"))},
            "scored_resources": summarize_containers([row for row in boxes if row["phase"] == "scored"], cpu_peaks),
            "calibration_resources": summarize_containers(
                [row for row in boxes if row["phase"] == "calibration"], cpu_peaks),
            "per_scenario": {scenario: summarize_rows([row for row in scored if row["scenario"] == scenario])
                             for scenario in sorted({row["scenario"] for row in mine})},
        }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summarize", type=Path, help="Only recompute summary.json for an existing run")
    parser.add_argument("--image", default="envfuzz:phase3-base")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed-plan", type=Path)
    parser.add_argument("--trials", type=int, default=3, help="Calibration trials per baseline")
    parser.add_argument("--repeats", type=int, default=3, help="Scored episodes per scenario and backend")
    parser.add_argument("--retries", type=int, default=2, help="Retries after an infrastructure result")
    parser.add_argument("--calibration-attempts", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=25.0, help="Simulated mission limit (s)")
    parser.add_argument("--watchdog", type=float, default=80.0, help="Wall-clock watchdog per episode (s)")
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="1536m")
    args = parser.parse_args()
    if args.summarize is None and (args.output is None or args.seed_plan is None):
        parser.error("--output and --seed-plan are required unless --summarize is given")
    args.output = args.output.resolve() if args.output else None
    return args


def main() -> None:
    args = parse_args()
    if args.summarize is not None:
        summarize(args.summarize.resolve())
        return
    args.output.mkdir(parents=True, exist_ok=False)
    for directory in ("resources", "console"):
        (args.output / directory).mkdir()
    suite = prepare_suite(args.output, args.seed_plan.resolve())
    record_host(args.output, args.image)
    plan = {key: (str(value) if isinstance(value, Path) else value) for key, value in vars(args).items()}
    plan.update(suite=suite, backends=BACKENDS, started_unix_s=time.time())
    (args.output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    stop = threading.Event()
    sampler = threading.Thread(target=sample_stats, args=(args.output / "docker-stats.jsonl", stop), daemon=True)
    sampler.start()
    workers = [Worker(args, backend, suite) for backend in BACKENDS]
    threads = [threading.Thread(target=worker.run) for worker in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    sampler.join()
    write_csv(args.output / "containers.csv", [row for worker in workers for row in worker.containers])
    write_csv(args.output / "results.csv", collect_rows(args.output))
    summarize(args.output)
    (args.output / "finished.json").write_text(json.dumps({"finished_unix_s": time.time()}) + "\n",
                                               encoding="utf-8")


if __name__ == "__main__":
    main()
