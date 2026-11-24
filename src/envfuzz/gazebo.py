"""Run an isolated Gazebo mission and persist its result and telemetry."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Callable
from uuid import uuid4
from xml.etree import ElementTree as ET

from .maps import export_keepout_filter_map, export_navigation_map, save_map_comparison
from .oracle import load_baseline, restricted_events
from .oracle_config import load_oracle
from .profile import SimulationProfile, load_profile
from .results import EpisodeResult, write_result_bundle
from .runner import _terminate
from .scenario import Scenario, compile_sdf, timeline_moves
from .scoring import apply_baseline, read_trace


def require_camera_capability() -> None:
    """Reject the optional RGB-D profile unless an NVIDIA GPU is exposed."""
    executable = shutil.which("nvidia-smi")
    if executable is None:
        raise OSError("The RTAB-Map camera profile requires an NVIDIA GPU exposed to the runtime")
    probe = subprocess.run(
        [executable, "--query-gpu=name", "--format=csv,noheader"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        raise OSError("The RTAB-Map camera profile could not detect an exposed NVIDIA GPU")


def _prepare_review_gui_config(scenario: Scenario, output: Path, environment: dict[str, str]) -> Path | None:
    source = environment.get("ENVFUZZ_GUI_CONFIG")
    if not source or not Path(source).is_file():
        return None
    contents = Path(source).read_text(encoding="utf-8")
    if contents.lstrip().startswith("<?xml"):
        contents = contents[contents.index("?>") + 2:]
    root = ET.fromstring(f"<envfuzz_gui_config>{contents}</envfuzz_gui_config>")
    camera_pose = root.find("./plugin[@name='3D View']/camera_pose")
    if camera_pose is None:
        return None
    world = scenario.world
    center_x = (world["min_x"] + world["max_x"]) / 2
    center_y = (world["min_y"] + world["max_y"]) / 2
    span = max(world["max_x"] - world["min_x"], world["max_y"] - world["min_y"])
    camera_pose.text = f"{center_x} {center_y} {span * 1.15} 0 1.35 0"
    target = output / "gui.config"
    body = "\n".join(ET.tostring(child, encoding="unicode") for child in root)
    target.write_text(f"<?xml version=\"1.0\"?>\n{body}\n", encoding="utf-8")
    environment["ENVFUZZ_GUI_CONFIG"] = str(target)
    return target


def run_gazebo(
    scenario: Scenario, output: Path, timeout: float, watchdog: float,
    params_file: Path | None = None, baseline_file: Path | None = None, oracle_file: Path | None = None,
    profile_file: Path | None = None, keepout_filter: bool = False, graphical: bool = False,
    on_graphical_ready: Callable[[bool], None] | None = None,
) -> EpisodeResult:
    if not scenario.goals:
        raise ValueError("A Gazebo mission requires at least one goal")
    profile = load_profile(profile_file)
    if profile is not None and profile.camera_profile:
        require_camera_capability()
    if keepout_filter and not scenario.restricted_zones:
        raise ValueError("Keepout Filter requires at least one restricted zone")
    world = compile_sdf(scenario, profile, include_scene_broadcaster=graphical)
    baseline = load_baseline(baseline_file) if baseline_file is not None else None
    oracle = load_oracle(oracle_file)
    if baseline is not None and (
        baseline.get("schema_version") != "1" or "position_error_m" not in baseline.get("thresholds", {})
    ):
        raise ValueError("This baseline lacks a Phase 1 position-error threshold. Run calibration again.")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "world.sdf").write_text(world, encoding="utf-8")
    (output / "scenario.json").write_text(scenario.canonical_json(), encoding="utf-8")
    resolved_profile = (profile or SimulationProfile()).resolved()
    localization_map = None
    if resolved_profile["navigation_backend"] == "amcl":
        localization_map = export_navigation_map(scenario, output)
    config = {"backend": "gazebo", "timeout": timeout, "watchdog": watchdog,
              "simulation_profile": resolved_profile, "keepout_filter": keepout_filter}
    if params_file is not None:
        shutil.copyfile(params_file, output / "nav2.yaml")
    if baseline_file is not None:
        shutil.copyfile(baseline_file, output / "baseline.json")
        (output / "oracle.yaml").write_text(oracle.model_dump_json(indent=2), encoding="utf-8")
    if profile is not None:
        (output / "profile.yaml").write_text(profile.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("ENVFUZZ_LIDAR_HZ", None)
    environment.pop("ENVFUZZ_LIDAR_RAYS", None)
    environment.pop("ENVFUZZ_LIDAR_FOV_DEG", None)
    environment.pop("ENVFUZZ_LIDAR_NOISE_M", None)
    environment.pop("ENVFUZZ_CAMERA_PROFILE", None)
    environment.pop("ENVFUZZ_CAMERA_HZ", None)
    environment.pop("ENVFUZZ_CAMERA_WIDTH", None)
    environment.pop("ENVFUZZ_CAMERA_HEIGHT", None)
    environment["ENVFUZZ_ROBOT_MODEL"] = resolved_profile["robot_model"]
    environment["ENVFUZZ_NAVIGATION_BACKEND"] = resolved_profile["navigation_backend"]
    environment.pop("ENVFUZZ_KEEPOUT_FILTER_MASK", None)
    if keepout_filter:
        environment["ENVFUZZ_KEEPOUT_FILTER_MASK"] = str(export_keepout_filter_map(scenario, output))
    if localization_map is not None:
        environment["ENVFUZZ_LOCALIZATION_MAP"] = str(localization_map)
    else:
        environment.pop("ENVFUZZ_LOCALIZATION_MAP", None)
    observation_flags = ("record_contacts", "record_cliff", "record_motion")
    for flag in observation_flags:
        environment.pop(f"ENVFUZZ_{flag.upper()}", None)
    environment.update(
        ROS_DOMAIN_ID=os.environ.get("ENVFUZZ_WORKER_DOMAIN", str(10 + secrets.randbelow(90))),
        ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST",
        ROS_STATIC_PEERS="",
        GZ_PARTITION=f"envfuzz-{uuid4().hex}",
        ROS_LOG_DIR=str(output / "ros-logs"),
    )
    config.update(ros_domain_id=environment["ROS_DOMAIN_ID"], gazebo_partition=environment["GZ_PARTITION"])
    (output / "run.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if profile is not None:
        environment.update(ENVFUZZ_LIDAR_HZ=str(profile.lidar_hz), ENVFUZZ_LIDAR_RAYS=str(profile.lidar_rays))
        if profile.lidar_fov_deg is not None:
            environment["ENVFUZZ_LIDAR_FOV_DEG"] = str(profile.lidar_fov_deg)
        if profile.lidar_noise_m is not None:
            environment["ENVFUZZ_LIDAR_NOISE_M"] = str(profile.lidar_noise_m)
        if profile.camera_profile:
            environment.update(
                ENVFUZZ_CAMERA_PROFILE="1",
                ENVFUZZ_CAMERA_HZ=str(profile.camera_hz),
                ENVFUZZ_CAMERA_WIDTH=str(profile.camera_width),
                ENVFUZZ_CAMERA_HEIGHT=str(profile.camera_height),
            )
        for flag in observation_flags:
            if getattr(profile, flag):
                environment[f"ENVFUZZ_{flag.upper()}"] = "1"
    if graphical:
        _prepare_review_gui_config(scenario, output, environment)
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
            if on_graphical_ready is not None:
                ready_deadline = time.monotonic() + min(30.0, watchdog)
                graphical_ready = False
                xwininfo = shutil.which("xwininfo")
                while time.monotonic() < ready_deadline:
                    if process.poll() is not None:
                        break
                    if xwininfo is not None:
                        try:
                            windows = subprocess.run(
                                [xwininfo, "-root", "-tree"], env=environment, capture_output=True,
                                text=True, timeout=2, check=False,
                            )
                        except (OSError, subprocess.TimeoutExpired):
                            time.sleep(0.25)
                            continue
                        if windows.returncode == 0 and "Gazebo Sim" in windows.stdout:
                            on_graphical_ready(True)
                            graphical_ready = True
                            break
                    else:
                        break
                    time.sleep(0.25)
                if not graphical_ready:
                    on_graphical_ready(False)
            process.wait(timeout=watchdog)
        if worker_result.exists() and process.returncode == 0:
            result = EpisodeResult.model_validate_json(worker_result.read_text(encoding="utf-8"))
            final_layout = _record_events(output, scenario)
            save_map_comparison(output, final_layout)
            if baseline is not None:
                apply_baseline(result, scenario, output, baseline, oracle)
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


def _record_events(output: Path, scenario: Scenario) -> Scenario:
    moves = timeline_moves(scenario)
    if not moves:
        return scenario
    log = (output / "logs.txt").read_text(encoding="utf-8", errors="replace")
    applied = {int(index): int(stamp) / 1e9 for index, stamp in re.findall(
        r"ENVFUZZ_EVENT (\d+) (\d+)", log,
    )}
    if "ENVFUZZ_EVENT_ERROR" in log or ("Failed to load system plugin" in log and "envfuzz_timeline" in log):
        raise ValueError("Gazebo could not apply the scheduled environment edits")
    states = {obj.id: obj for obj in scenario.objects}
    records = []
    actor_ids = {actor.object_id for actor in scenario.actors or ()}
    for index, event in enumerate(moves, 1):
        record = {"object_id": event.object_id, "requested_sim_time": event.at,
                  "applied_sim_time": applied.get(index)}
        if event.object_id in actor_ids:
            record["pose"] = event.pose.model_dump()
        records.append(record)
        if index in applied:
            states[event.object_id] = states[event.object_id].model_copy(update={"pose": event.pose})
    (output / "events-applied.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return scenario.model_copy(update={"objects": list(states.values())})
