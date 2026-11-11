"""Calibrate and apply the Phase 1 oracle to recorded missions."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .oracle import calibrate, pose_error_events, restricted_events, score_with_baseline, stuck_events
from .oracle_config import OracleConfig, detectors
from .maps import reference_grid
from .profile import load_profile
from .results import EpisodeEvent, EpisodeResult, PoseSample
from .scenario import Scenario


def mission_identity(scenario: Scenario, params: Path) -> dict[str, str]:
    canonical = scenario.canonical_dict()
    mission = {key: canonical[key] for key in ("world", "start", "goals", "restricted_zones")}
    identity = {
        "world_mission_sha256": hashlib.sha256(json.dumps(mission, sort_keys=True).encode()).hexdigest(),
        "nav2_params_sha256": hashlib.sha256(params.read_bytes()).hexdigest(),
    }
    profile = params.with_name("profile.yaml")
    if profile.is_file():
        identity["simulation_profile_sha256"] = hashlib.sha256(profile.read_bytes()).hexdigest()
        if load_profile(profile).resolved()["navigation_backend"] == "amcl":
            layout = reference_grid(scenario, resolution=0.05).model_dump()
            identity["localization_map_sha256"] = hashlib.sha256(
                json.dumps(layout, sort_keys=True).encode()
            ).hexdigest()
    return identity


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
    map_errors = []
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
        map_path = trial / "map-metrics.json"
        if map_path.is_file():
            error = json.loads(map_path.read_text(encoding="utf-8"))["map_error"]
            if error is not None:
                map_errors.append(error)
    data = calibrate(maxima, {**identity, "scenario_hash": scenario.content_hash()})
    data["distributions"] = {"position_error_max_m": maxima, "mission_duration_sim_s": durations}
    data["thresholds"] = {"position_error_m": data["thresholds"]["metric"]}
    if len(map_errors) == len(trials):
        data["distributions"]["map_error"] = map_errors
        data["thresholds"]["map_error"] = min(1.0, calibrate(map_errors, identity)["thresholds"]["metric"])
    data["completed_trials"] = len(trials)
    return data


def apply_baseline(
    result: EpisodeResult, scenario: Scenario, bundle: Path, baseline: dict,
    oracle: OracleConfig | None = None,
) -> None:
    oracle = oracle if oracle is not None else OracleConfig()
    identity = mission_identity(scenario, bundle / "nav2.yaml")
    stored_identity = baseline.get("identity", {})
    if any(stored_identity.get(key) != identity.get(key) for key in
           set(identity) | {"simulation_profile_sha256"}):
        raise ValueError("Baseline world, mission, or Nav2 parameters do not match this run")
    threshold = baseline.get("thresholds", {}).get("position_error_m")
    if not isinstance(threshold, (float, int)) or not math.isfinite(threshold) or threshold < 0:
        raise ValueError("Baseline is missing a valid position-error threshold. Recalibrate the baseline.")
    truth, estimates = read_trace(bundle / "telemetry.jsonl")
    result.events = (
        stuck_events(truth, epsilon=oracle.stuck_displacement_m, window=oracle.stuck_window_s,
                     mission_complete=result.status == "completed",
                     baseline_complete=baseline.get("completed_trials", 0) > 0)
        + pose_error_events(truth, estimates, threshold, duration=oracle.position_window_s, max_gap=0.25)
        + restricted_events(truth, scenario.restricted_zones, radius=0.22)
    )
    metrics_path = bundle / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.is_file() else {}
    result.events += recovery_events(metrics.get("recoveries", []), oracle.recovery_count, oracle.recovery_window_s)
    map_path = bundle / "map-metrics.json"
    if map_path.is_file() and "map_error" in baseline["thresholds"]:
        comparison = json.loads(map_path.read_text(encoding="utf-8"))
        error = comparison["map_error"]
        if error is not None and comparison["coverage"] >= oracle.min_map_coverage and (
            error > baseline["thresholds"]["map_error"]
        ):
            result.events.append(EpisodeEvent(tier="T3", kind="map_error",
                sim_start=truth[-1].sim_time if truth else result.sim_duration,
                measured_value=error, details=comparison))
    for detector in detectors(oracle):
        result.events += [EpisodeEvent.model_validate(event) for event in detector(
            scenario=scenario, bundle=bundle, result=result, baseline=baseline, config=oracle,
        )]
    result.events = [event for event in result.events if event.tier in oracle.tiers]
    for event in result.events:
        event.details["tier_label"] = oracle.tiers[event.tier].label
    points = {tier: setting.points for tier, setting in oracle.tiers.items()}
    result.score, result.tiers_hit = score_with_baseline(result.events, baseline, points)
    result.baseline_sha256 = hashlib.sha256(
        json.dumps(baseline, sort_keys=True).encode()
    ).hexdigest()


def recovery_events(recoveries: list[dict], count: int, window: float) -> list[EpisodeEvent]:
    for index, event in enumerate(recoveries[count - 1:], count - 1):
        start = recoveries[index - count + 1]["sim_time"]
        if event["sim_time"] - start <= window:
            return [EpisodeEvent(tier="T1", kind="recovery_loop", sim_start=start,
                                 sim_end=event["sim_time"], measured_value=count)]
    return []
