"""Versioned normalized samples and durable episode result bundles."""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
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
    tier: Literal["T1", "T2", "T4"]
    kind: str
    sim_start: float
    sim_end: float | None = None
    measured_value: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EpisodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = RESULT_SCHEMA_VERSION
    status: Literal["completed", "timeout", "watchdog", "interrupted", "launch_failed", "simulator_failed"]
    classification: Literal["behavior", "infrastructure", "interrupted"]
    score: int = 0
    tiers_hit: list[str] = Field(default_factory=list)
    events: list[EpisodeEvent] = Field(default_factory=list)
    sim_duration: float = 0.0
    wall_duration: float = 0.0
    real_time_factor: float | None = None
    reason: str | None = None


def runtime_versions() -> dict[str, str]:
    return {
        "envfuzz": "0.1.0",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ros": "jazzy (container)",
        "gazebo": "harmonic (container)",
    }


def write_result_bundle(
    directory: str | Path,
    scenario: Any,
    world: str,
    result: EpisodeResult,
    logs: str = "",
    rosbag: str | None = None,
) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    (root / "scenario.json").write_text(json.dumps(scenario.canonical_dict(), indent=2, sort_keys=True) + "\n")
    (root / "world.sdf").write_text(world, encoding="utf-8")
    (root / "result.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "versions.json").write_text(json.dumps(runtime_versions(), indent=2, sort_keys=True) + "\n")
    (root / "logs.txt").write_text(logs, encoding="utf-8")
    (root / "timings.json").write_text(
        json.dumps({"sim_duration": result.sim_duration, "wall_duration": result.wall_duration,
                    "real_time_factor": result.real_time_factor}, indent=2) + "\n",
        encoding="utf-8",
    )
    if rosbag is not None:
        (root / "rosbag").write_text(rosbag, encoding="utf-8")
    return root

