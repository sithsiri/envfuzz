"""Baseline calibration and explainable Phase 1 oracle detectors."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Protocol

from .results import EpisodeEvent, PoseSample


class Detector(Protocol):
    def detect(self, samples: list[PoseSample], **kwargs: Any) -> list[EpisodeEvent]: ...


def align_pose(sample: PoseSample, origin_x: float, origin_y: float, origin_yaw: float = 0.0) -> PoseSample:
    dx, dy = sample.x - origin_x, sample.y - origin_y
    c, s = math.cos(-origin_yaw), math.sin(-origin_yaw)
    return sample.model_copy(update={"x": dx * c - dy * s, "y": dx * s + dy * c, "yaw": sample.yaw - origin_yaw})


def stuck_events(
    samples: list[PoseSample], epsilon: float, window: float, mission_complete: bool = False,
    baseline_complete: bool = True,
) -> list[EpisodeEvent]:
    if mission_complete or not baseline_complete or not samples:
        return []
    start = samples[0]
    for sample in samples[1:]:
        if sample.sim_time - start.sim_time >= window:
            displacement = math.hypot(sample.x - start.x, sample.y - start.y)
            if displacement <= epsilon:
                return [EpisodeEvent(tier="T1", kind="stuck", sim_start=start.sim_time,
                                     sim_end=sample.sim_time, measured_value=displacement)]
            start = sample
    return []


def pose_error_events(
    ground_truth: list[PoseSample], slam: list[PoseSample], threshold: float, duration: float
) -> list[EpisodeEvent]:
    if not ground_truth or not slam:
        return []
    errors = []
    for truth, estimate in zip(ground_truth, slam):
        errors.append((truth.sim_time, math.hypot(truth.x - estimate.x, truth.y - estimate.y)))
    begin: float | None = None
    for timestamp, error in errors:
        if error > threshold:
            begin = timestamp if begin is None else begin
            if timestamp - begin >= duration:
                return [EpisodeEvent(tier="T2", kind="pose_error", sim_start=begin,
                                     sim_end=timestamp, measured_value=error)]
        else:
            begin = None
    return []


def _inside(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def restricted_events(samples: list[PoseSample], zones: list[Any]) -> list[EpisodeEvent]:
    events: list[EpisodeEvent] = []
    for zone in zones:
        begin: float | None = None
        for sample in samples:
            if _inside(sample.x, sample.y, zone.polygon):
                begin = sample.sim_time if begin is None else begin
            elif begin is not None:
                events.append(EpisodeEvent(tier="T4", kind="restricted_area", sim_start=begin,
                                            sim_end=sample.sim_time, details={"zone": zone.id}))
                begin = None
        if begin is not None and samples:
            events.append(EpisodeEvent(tier="T4", kind="restricted_area", sim_start=begin,
                                       sim_end=samples[-1].sim_time, details={"zone": zone.id}))
    return events


def score_events(events: list[EpisodeEvent], tier_points: dict[str, int] | None = None) -> tuple[int, list[str]]:
    points = tier_points or {"T1": 1, "T2": 2, "T4": 4}
    tiers = sorted({event.tier for event in events}, key=lambda tier: points.get(tier, 0), reverse=True)
    return (max((points.get(event.tier, 0) for event in events), default=0), tiers)


def score_with_baseline(
    events: list[EpisodeEvent],
    baseline: dict[str, Any] | None,
    tier_points: dict[str, int] | None = None,
) -> tuple[int, list[str]]:
    """Score only against an explicitly supplied calibration bundle."""
    if baseline is None:
        raise ValueError("baseline is required for scored runs")
    if baseline.get("schema_version") != "1":
        raise ValueError("unsupported baseline schema version")
    return score_events(events, tier_points)


def calibrate(values: list[float], identity: dict[str, str], floor: float = 0.1) -> dict[str, Any]:
    if not values:
        raise ValueError("at least one baseline value is required")
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    threshold = max(floor, mean + 3 * stdev)
    return {"schema_version": "1", "identity": identity, "distributions": {"metric": values},
            "thresholds": {"metric": threshold}, "formula": "max(floor, mean + 3*population_stddev)",
            "floor": floor}


def save_baseline(data: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_baseline(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)
