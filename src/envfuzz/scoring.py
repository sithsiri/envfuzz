"""Calibrate and apply the Phase 1 oracle to recorded missions."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .oracle import calibrate, pose_error_events, restricted_events, score_with_baseline, stuck_events
from .results import EpisodeResult, PoseSample
from .scenario import Scenario


def mission_identity(scenario: Scenario, params: Path) -> dict[str, str]:
    canonical = scenario.canonical_dict()
    mission = {key: canonical[key] for key in ("world", "start", "goals", "restricted_zones")}
    return {
        "world_mission_sha256": hashlib.sha256(json.dumps(mission, sort_keys=True).encode()).hexdigest(),
        "nav2_params_sha256": hashlib.sha256(params.read_bytes()).hexdigest(),
    }


def read_trace(path: Path) -> tuple[list[PoseSample], list[PoseSample]]:
    truth, estimates = [], []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            truth.append(PoseSample.model_validate(row["ground_truth"]))
            if row["estimated"] is not None:
                estimates.append(PoseSample.model_validate(row["estimated"]))
    return truth, estimates


def baseline_from_trials(scenario: Scenario, trials: list[Path], durations: list[float]) -> dict:
    identity = mission_identity(scenario, trials[0] / "nav2.yaml")
    maxima = []
    for trial in trials:
        if mission_identity(scenario, trial / "nav2.yaml") != identity:
            raise ValueError("Nav2 parameters changed between baseline trials")
        truth, estimates = read_trace(trial / "telemetry.jsonl")
        truth_by_time = {sample.sim_time: sample for sample in truth}
        if not estimates:
            raise ValueError(f"No paired pose samples in baseline trial {trial.name}")
        maxima.append(max(
            math.hypot(truth_by_time[estimate.sim_time].x - estimate.x,
                       truth_by_time[estimate.sim_time].y - estimate.y)
            for estimate in estimates
        ))
    data = calibrate(maxima, {**identity, "scenario_hash": scenario.content_hash()})
    data["distributions"] = {"position_error_max_m": maxima, "mission_duration_sim_s": durations}
    data["thresholds"] = {"position_error_m": data["thresholds"]["metric"]}
    data["completed_trials"] = len(trials)
    return data


def apply_baseline(
    result: EpisodeResult, scenario: Scenario, bundle: Path, baseline: dict,
) -> None:
    identity = mission_identity(scenario, bundle / "nav2.yaml")
    stored_identity = baseline.get("identity", {})
    if any(stored_identity.get(key) != value for key, value in identity.items()):
        raise ValueError("Baseline world, mission, or Nav2 parameters do not match this run")
    threshold = baseline.get("thresholds", {}).get("position_error_m")
    if not isinstance(threshold, (float, int)) or not math.isfinite(threshold) or threshold < 0:
        raise ValueError("Baseline is missing a valid position-error threshold. Recalibrate the baseline.")
    truth, estimates = read_trace(bundle / "telemetry.jsonl")
    result.events = (
        stuck_events(truth, epsilon=0.05, window=5.0,
                     mission_complete=result.status == "completed",
                     baseline_complete=baseline.get("completed_trials", 0) > 0)
        + pose_error_events(truth, estimates, threshold, duration=1.0, max_gap=0.25)
        + restricted_events(truth, scenario.restricted_zones, radius=0.22)
    )
    result.score, result.tiers_hit = score_with_baseline(result.events, baseline)
    result.baseline_sha256 = hashlib.sha256(
        json.dumps(baseline, sort_keys=True).encode()
    ).hexdigest()
