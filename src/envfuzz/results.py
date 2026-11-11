"""Versioned normalized samples and durable episode result bundles."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RESULT_SCHEMA_VERSION = "1"


class PoseSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sim_time: float = Field(ge=0)
    x: float
    y: float
    yaw: float = 0.0


class EpisodeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Literal["T1", "T2", "T3", "T4"]
    kind: str
    sim_start: float
    sim_end: float | None = None
    measured_value: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EpisodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = RESULT_SCHEMA_VERSION
    status: Literal[
        "completed", "mission_failed", "timeout", "watchdog", "interrupted",
        "launch_failed", "simulator_failed",
    ]
    classification: Literal["behavior", "infrastructure", "interrupted"]
    score: int = 0
    tiers_hit: list[str] = Field(default_factory=list)
    events: list[EpisodeEvent] = Field(default_factory=list)
    sim_duration: float = 0.0
    wall_duration: float = 0.0
    real_time_factor: float | None = None
    reason: str | None = None
    baseline_sha256: str | None = None


def runtime_versions() -> dict[str, str]:
    versions = {
        "envfuzz": "0.1.0",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ros": os.environ.get("ROS_DISTRO", "unavailable"),
        "gazebo": os.environ.get("GZ_VERSION", "unknown"),
    }
    if versions["ros"] == "jazzy" and shutil.which("dpkg-query"):
        packages = [
            "ros-jazzy-gz-sim-vendor", "ros-jazzy-ros-gz-bridge", "ros-jazzy-ros-gz-sim",
            "ros-jazzy-nav2-bringup", "ros-jazzy-slam-toolbox", "ros-jazzy-rtabmap-ros",
            "ros-jazzy-nav2-minimal-tb3-sim",
        ]
        output = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}\t${Version}\n", *packages],
            capture_output=True, text=True, check=False,
        )
        versions.update(line.split("\t", 1) for line in output.stdout.splitlines() if "\t" in line)
    if shutil.which("gz"):
        try:
            output = subprocess.run(
                ["gz", "sim", "--versions"], capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        else:
            if output.returncode == 0 and output.stdout.strip():
                versions["gazebo_sim"] = ", ".join(output.stdout.splitlines())
    return versions


def write_result_bundle(
    directory: str | Path,
    scenario: Any,
    world: str,
    result: EpisodeResult,
    logs: str | None = "",
    rosbag: str | None = None,
) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    (root / "scenario.json").write_text(json.dumps(scenario.canonical_dict(), indent=2, sort_keys=True) + "\n")
    (root / "world.sdf").write_text(world, encoding="utf-8")
    (root / "versions.json").write_text(json.dumps(runtime_versions(), indent=2, sort_keys=True) + "\n")
    if logs is not None:
        (root / "logs.txt").write_text(logs, encoding="utf-8")
    (root / "timings.json").write_text(
        json.dumps({"sim_duration": result.sim_duration, "wall_duration": result.wall_duration,
                    "real_time_factor": result.real_time_factor}, indent=2) + "\n",
        encoding="utf-8",
    )
    if rosbag is not None:
        (root / "rosbag").write_text(rosbag, encoding="utf-8")
    pending_result = root / "result.json.tmp"
    pending_result.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    pending_result.replace(root / "result.json")
    return root

