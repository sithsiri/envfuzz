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
    ground_truth: list[PoseSample], slam: list[PoseSample], threshold: float, duration: float,
    max_gap: float | None = None,
) -> list[EpisodeEvent]:
    if not ground_truth or not slam:
        return []
    estimates = {sample.sim_time: sample for sample in slam}
    begin: float | None = None
    previous: float | None = None
    for truth in ground_truth:
        timestamp = truth.sim_time
        estimate = estimates.get(timestamp)
        if estimate is None or (
            max_gap is not None and previous is not None and timestamp - previous > max_gap
        ):
            begin = None
        previous = timestamp
        if estimate is None:
            continue
        error = math.hypot(truth.x - estimate.x, truth.y - estimate.y)
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


def _touches_boundary(x: float, y: float, polygon: list[tuple[float, float]], radius: float) -> bool:
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        dx, dy = x2 - x1, y2 - y1
        length_squared = dx * dx + dy * dy
        t = ((x - x1) * dx + (y - y1) * dy) / length_squared if length_squared else 0
        t = min(1.0, max(0.0, t))
        if math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) <= radius:
            return True
    return False


def restricted_events(samples: list[PoseSample], zones: list[Any], radius: float = 0) -> list[EpisodeEvent]:
    events: list[EpisodeEvent] = []
    for zone in zones:
        begin: float | None = None
        for sample in samples:
            if _inside(sample.x, sample.y, zone.polygon) or (
                radius > 0 and _touches_boundary(sample.x, sample.y, zone.polygon, radius)
            ):
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
    tiers = sorted({event.tier for event in events}, key=lambda tier: (-points.get(tier, 0), tier))
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
