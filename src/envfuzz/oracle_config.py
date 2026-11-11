"""Versioned detector settings and trusted local detector extensions."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Tier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    points: int = Field(ge=0)


class OracleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    tiers: dict[Literal["T1", "T2", "T3", "T4"], Tier] = Field(default_factory=lambda: {
        "T1": Tier(label="Stall or recovery loop", points=1),
        "T2": Tier(label="Position error", points=2),
        "T3": Tier(label="Map error", points=3),
        "T4": Tier(label="Restricted area", points=4),
    })
    stuck_displacement_m: float = Field(default=0.05, ge=0)
    stuck_window_s: float = Field(default=5, gt=0)
    position_window_s: float = Field(default=1, gt=0)
    min_map_coverage: float = Field(default=0.2, ge=0, le=1)
    recovery_count: int = Field(default=3, ge=1)
    recovery_window_s: float = Field(default=10, gt=0)
    extensions: list[str] = Field(default_factory=list)


def load_oracle(path: Path | None) -> OracleConfig:
    return OracleConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))) if path else OracleConfig()


def detectors(config: OracleConfig):
    installed = {entry.name: entry for entry in entry_points(group="envfuzz.detectors")}
    for name in config.extensions:
        if name not in installed:
            raise ValueError(f"Oracle detector is not installed: {name}")
        yield installed[name].load()
