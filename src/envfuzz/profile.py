"""Reproducible simulation timing and sensor settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


RobotModel = Literal["turtlebot3_waffle", "compact_vacuum"]
NavigationBackend = Literal["slam_toolbox"]


class SimulationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    step_s: float = Field(default=0.001, ge=0.0001, le=0.02)
    real_time_factor: float = Field(default=1.0, ge=0.1, le=20)
    lidar_hz: float = Field(default=5, ge=1, le=30)
    lidar_rays: int = Field(default=360, ge=90, le=1440)
    record_contacts: bool | None = None
    record_cliff: bool | None = None
    record_motion: bool | None = None
    robot_model: RobotModel | None = None
    navigation_backend: NavigationBackend | None = None
    lidar_fov_deg: float | None = Field(default=None, ge=90, le=360)
    lidar_noise_m: float | None = Field(default=None, ge=0, le=0.05)

    def resolved(self) -> dict:
        settings = self.model_dump(exclude_none=True)
        settings["robot_model"] = self.robot_model or "turtlebot3_waffle"
        settings["navigation_backend"] = self.navigation_backend or "slam_toolbox"
        settings["lidar_fov_deg"] = 360 if self.lidar_fov_deg is None else self.lidar_fov_deg
        return settings


def load_profile(path: Path | None) -> SimulationProfile | None:
    return SimulationProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))) if path else None
