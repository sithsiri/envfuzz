"""Run episodes in resource-limited Docker containers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import time

from .results import EpisodeResult
from .scenario import Scenario


@dataclass(frozen=True)
class WorkerLimits:
    image: str = "envfuzz:jazzy"
    cpus: float = 2.0
    memory: str = "1536m"


def container_name(bundle: Path) -> str:
    return "envfuzz-" + hashlib.sha256(str(bundle.resolve()).encode()).hexdigest()[:24]


def remove_worker(bundle: Path) -> None:
    name = container_name(bundle)
    try:
        info = subprocess.run(
            ["docker", "inspect", "--format", '{{index .Config.Labels "org.envfuzz.worker"}}', name],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if info.returncode == 0 and info.stdout.strip() == name:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_worker(
    scenario: Scenario, bundle: Path, timeout: float, watchdog: float, limits: WorkerLimits,
    *, baseline: Path | None = None, params: Path | None = None, read_only: bool = False,
    oracle: Path | None = None, profile: Path | None = None,
    domain_id: int | None = None,
) -> EpisodeResult:
    bundle = bundle.resolve()
    attempt = bundle.parent
    attempt.mkdir(parents=True, exist_ok=False)
    inputs = attempt / "inputs"
    inputs.mkdir()
    results = attempt / "outputs"
    results.mkdir()
    (inputs / "scenario.json").write_text(scenario.canonical_json(), encoding="utf-8")
    name = container_name(bundle)
    (attempt / "worker.json").write_text(json.dumps({
        "container": name, "image": limits.image, "cpus": limits.cpus,
        "memory": limits.memory, "network": "none", "read_only_root": read_only,
        "read_only_inputs": True, "ros_domain_id": domain_id,
    }, indent=2) + "\n", encoding="utf-8")
    cmd = [
        "docker", "run", "--name", name, "--rm", "--network", "none",
        "--label", f"org.envfuzz.worker={name}",
        "--cpus", str(limits.cpus), "--memory", limits.memory,
        "--mount", f"type=bind,src={inputs},dst=/inputs,readonly",
        "--mount", f"type=bind,src={results},dst=/results",
    ]
    if read_only:
        cmd += ["--read-only", "--tmpfs", "/tmp:rw,nosuid", "--tmpfs", "/root/.gz:rw,nosuid",
                "--env", "MESA_SHADER_CACHE_DISABLE=true"]
    if domain_id is not None:
        cmd += ["--env", f"ENVFUZZ_WORKER_DOMAIN={domain_id}"]
    options = []
    for source, target, flag in (
        (baseline, "baseline.json", "--baseline"), (params, "nav2.yaml", "--params-file"),
        (oracle, "oracle.yaml", "--oracle"), (profile, "profile.yaml", "--profile"),
    ):
        if source is not None:
            (inputs / target).write_bytes(source.read_bytes())
            options += [flag, f"/inputs/{target}"]
    cmd += [
        limits.image, "run", "/inputs/scenario.json", "--output", f"/results/{bundle.name}",
        "--timeout", str(timeout), "--watchdog", str(watchdog), *options,
    ]

    start = time.monotonic()
    status = "simulator_failed"
    reason = "Worker exited without a result"
    try:
        with (attempt / "worker.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                       check=False, timeout=watchdog + 20)
        delivered = results / bundle.name
        if delivered.exists():
            delivered.replace(bundle)
        result = bundle / "result.json"
        if result.is_file():
            return EpisodeResult.model_validate_json(result.read_text(encoding="utf-8"))
        if completed.returncode == 125:
            status = "launch_failed"
        reason = f"Docker exited with code {completed.returncode}. See worker.log."
    except subprocess.TimeoutExpired:
        status, reason = "watchdog", "Container exceeded its wall-clock deadline"
    except KeyboardInterrupt:
        status, reason = "interrupted", "Evaluation interrupted"
    except OSError as error:
        status, reason = "launch_failed", str(error)
    except ValueError as error:
        reason = f"Worker returned an invalid result: {error}"
    finally:
        remove_worker(bundle)
    result = EpisodeResult(
        status=status, classification="interrupted" if status == "interrupted" else "infrastructure",
        reason=reason, wall_duration=time.monotonic() - start,
    )
    bundle.mkdir(exist_ok=True)
    pending = bundle / "result.json.tmp"
    pending.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    pending.replace(bundle / "result.json")
    return result
