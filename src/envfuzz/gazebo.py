"""Run an isolated Gazebo mission and persist its result and telemetry."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from .oracle import load_baseline, restricted_events
from .results import EpisodeResult, write_result_bundle
from .runner import _terminate
from .scenario import Scenario, compile_sdf
from .scoring import apply_baseline, read_trace


def run_gazebo(
    scenario: Scenario, output: Path, timeout: float, watchdog: float,
    params_file: Path | None = None, baseline_file: Path | None = None,
) -> EpisodeResult:
    if not scenario.goals:
        raise ValueError("A Gazebo mission requires at least one goal")
    world = compile_sdf(scenario)
    baseline = load_baseline(baseline_file) if baseline_file is not None else None
    if baseline is not None and (
        baseline.get("schema_version") != "1" or "position_error_m" not in baseline.get("thresholds", {})
    ):
        raise ValueError("This baseline lacks a Phase 1 position-error threshold. Run calibration again.")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "world.sdf").write_text(world, encoding="utf-8")
    (output / "scenario.json").write_text(scenario.canonical_json(), encoding="utf-8")
    config = {"backend": "gazebo", "timeout": timeout, "watchdog": watchdog}
    if params_file is not None:
        shutil.copyfile(params_file, output / "nav2.yaml")
    if baseline_file is not None:
        shutil.copyfile(baseline_file, output / "baseline.json")
    (output / "run.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        ROS_DOMAIN_ID=str(10 + secrets.randbelow(90)),
        ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST",
        ROS_STATIC_PEERS="",
        GZ_PARTITION=f"envfuzz-{uuid4().hex}",
        ROS_LOG_DIR=str(output / "ros-logs"),
    )
    process = None
    started = time.monotonic()
    worker_result = output / "mission.json"
    try:
        with (output / "logs.txt").open("w", encoding="utf-8") as logs:
            if not shutil.which("ros2") or importlib.util.find_spec("rclpy") is None:
                raise FileNotFoundError("Source the ROS 2 Jazzy and envfuzz_sim setup scripts first")
            process = subprocess.Popen(
                [sys.executable, "-m", "envfuzz.mission", str(output), str(timeout)],
                env=environment, stdout=logs, stderr=subprocess.STDOUT, start_new_session=True,
            )
            process.wait(timeout=watchdog)
        if worker_result.exists() and process.returncode == 0:
            result = EpisodeResult.model_validate_json(worker_result.read_text(encoding="utf-8"))
            if baseline is not None:
                apply_baseline(result, scenario, output, baseline)
            elif scenario.restricted_zones:
                truth, _ = read_trace(output / "telemetry.jsonl")
                result.events = restricted_events(truth, scenario.restricted_zones, radius=0.22)
        else:
            result = EpisodeResult(status="simulator_failed", classification="infrastructure",
                                   reason=f"Mission worker exited with code {process.returncode}. See logs.txt.")
    except ValueError as error:
        result = EpisodeResult(status="simulator_failed", classification="infrastructure",
                               reason=f"Invalid mission result: {error}")
    except OSError as error:
        result = EpisodeResult(status="launch_failed", classification="infrastructure", reason=str(error))
    except subprocess.TimeoutExpired:
        result = EpisodeResult(status="watchdog", classification="infrastructure",
                               reason="Wall-clock watchdog expired. See logs.txt.")
    except KeyboardInterrupt:
        result = EpisodeResult(status="interrupted", classification="interrupted",
                               reason="User interrupted the mission")
    finally:
        if process is not None:
            _terminate(process)
    wall_duration = time.monotonic() - started
    result.wall_duration = wall_duration
    if result.sim_duration > 0:
        result.real_time_factor = result.sim_duration / wall_duration
    write_result_bundle(output, scenario, world, result, logs=None)
    worker_result.unlink(missing_ok=True)
    return result
