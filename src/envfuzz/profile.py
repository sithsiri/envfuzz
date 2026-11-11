"""Reproducible simulation timing and sensor settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SimulationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    step_s: float = Field(default=0.001, ge=0.0001, le=0.02)
    real_time_factor: float = Field(default=1.0, ge=0.1, le=20)
    lidar_hz: float = Field(default=5, ge=1, le=30)
    lidar_rays: int = Field(default=360, ge=90, le=1440)


def load_profile(path: Path | None) -> SimulationProfile | None:
    return SimulationProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))) if path else None
