"""Check trusted, offline pose-observation fixtures against the benchmark oracle."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .oracle import pose_error_events, score_events
from .oracle_config import OracleConfig
from .results import PoseSample


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    time_s: float = Field(ge=0)
    truth_x_m: float
    estimate_x_m: float | None


class SensorFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    observations: list[Observation] = Field(min_length=2)
    expected_tiers: list[Literal["T2"]]

    @model_validator(mode="after")
    def check_order(self) -> SensorFixture:
        if any(first.time_s >= second.time_s for first, second in zip(
            self.observations, self.observations[1:],
        )):
            raise ValueError("Observation times must be strictly increasing")
        return self


class FixtureSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    version: Literal[1]
    position_threshold_m: float = Field(ge=0)
    fixtures: list[SensorFixture] = Field(min_length=1)

    @model_validator(mode="after")
    def check_ids(self) -> FixtureSuite:
        if len({fixture.id for fixture in self.fixtures}) != len(self.fixtures):
            raise ValueError("Sensor fixture IDs must be unique")
        return self


def check_sensor_fixtures(path: Path) -> dict:
    """Evaluate prerecorded observations without starting any ROS processes."""
    data = path.read_bytes()
    suite = FixtureSuite.model_validate_json(data)
    oracle = OracleConfig()
    points = {tier: setting.points for tier, setting in oracle.tiers.items()}
    checks = []
    for fixture in suite.fixtures:
        truth = [
            PoseSample(sim_time=row.time_s, x=row.truth_x_m, y=0)
            for row in fixture.observations
        ]
        estimates = [
            PoseSample(sim_time=row.time_s, x=row.estimate_x_m, y=0)
            for row in fixture.observations if row.estimate_x_m is not None
        ]
        events = pose_error_events(
            truth, estimates, suite.position_threshold_m,
            duration=oracle.position_window_s, max_gap=0.25,
        )
        score, tiers = score_events(events, points)
        checks.append({
            "id": fixture.id,
            "expected_tiers": fixture.expected_tiers,
            "observed_tiers": tiers,
            "score": score,
            "passed": tiers == fixture.expected_tiers,
        })
    return {
        "version": suite.version,
        "fixture_sha256": sha256(data).hexdigest(),
        "passed": all(check["passed"] for check in checks),
        "fixtures": checks,
    }
