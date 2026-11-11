"""Unit tests for oracle detectors, scoring, result bundles, fixtures and the sequential fuzzer."""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from envfuzz import oracle_config, results
from envfuzz.fuzzer import candidates, fuzz
from envfuzz.oracle import (
    align_pose, calibrate, load_baseline, save_baseline, score_with_baseline, stuck_events,
)
from envfuzz.oracle_config import OracleConfig, detectors, load_oracle
from envfuzz.results import EpisodeEvent, EpisodeResult, PoseSample, runtime_versions, write_result_bundle
from envfuzz.scenario import Scenario, validate_scenario
from envfuzz.scoring import apply_baseline, baseline_from_trials, mission_identity, recovery_events
from envfuzz.sensor_fixtures import FixtureSuite, SensorFixture

# --- oracle -----------------------------------------------------------------------------------


def test_align_pose_translates_then_rotates_into_origin_frame():
    # Arrange
    sample = PoseSample(sim_time=2.0, x=1.0, y=2.0, yaw=math.pi / 2)

    # Act
    aligned = align_pose(sample, origin_x=1.0, origin_y=1.0, origin_yaw=math.pi / 2)

    # Assert
    assert (aligned.x, aligned.y, aligned.yaw) == pytest.approx((1.0, 0.0, 0.0))
    assert aligned.sim_time == 2.0
    assert sample.x == 1.0  # original sample is unchanged


def test_stuck_detector_restarts_window_after_progress():
    # Arrange: moves during the first window, then stalls during the second.
    samples = [PoseSample(sim_time=t, x=x, y=0.0)
               for t, x in ((0, 0.0), (5, 1.0), (7, 1.01), (10, 1.02))]

    # Act
    events = stuck_events(samples, epsilon=0.05, window=5)

    # Assert
    assert [(e.kind, e.sim_start, e.sim_end) for e in events] == [("stuck", 5, 10)]
    assert events[0].measured_value == pytest.approx(0.02)


def test_stuck_detector_reports_nothing_while_robot_keeps_moving():
    # Arrange
    samples = [PoseSample(sim_time=float(t), x=0.5 * t, y=0.0) for t in range(12)]

    # Act / Assert
    assert stuck_events(samples, epsilon=0.05, window=5) == []
    assert stuck_events(samples[:1], epsilon=0.05, window=5) == []


def test_scored_runs_need_a_supported_baseline():
    # Arrange
    events = [EpisodeEvent(tier="T2", kind="pose_error", sim_start=1.0)]

    # Act / Assert
    assert score_with_baseline(events, {"schema_version": "1"}) == (2, ["T2"])
    with pytest.raises(ValueError, match="baseline is required"):
        score_with_baseline(events, None)
    with pytest.raises(ValueError, match="unsupported baseline schema"):
        score_with_baseline(events, {"schema_version": "0"})


def test_calibration_requires_values_and_round_trips_through_disk(tmp_path):
    # Arrange
    baseline = calibrate([0.1, 0.3], {"world": "abc"}, floor=0.05)
    path = tmp_path / "baseline.json"

    # Act
    save_baseline(baseline, path)

    # Assert
    assert load_baseline(path) == baseline
    assert baseline["thresholds"]["metric"] == pytest.approx(0.2 + 3 * 0.1)
    with pytest.raises(ValueError, match="at least one baseline value"):
        calibrate([], {})


# --- oracle configuration ---------------------------------------------------------------------


def _entry_points(*names):
    def detector(**context):
        return [{"tier": "T3", "kind": f"{names[0]}_detector", "sim_start": 0.5}]
    return lambda group: [SimpleNamespace(name=name, load=lambda: detector) for name in names]


def test_detector_extensions_must_be_installed(monkeypatch):
    # Arrange
    monkeypatch.setattr(oracle_config, "entry_points", _entry_points("glare"))

    # Act
    loaded = list(detectors(OracleConfig(extensions=["glare"])))

    # Assert
    assert loaded[0]()[0]["kind"] == "glare_detector"
    with pytest.raises(ValueError, match="not installed: fog"):
        list(detectors(OracleConfig(extensions=["fog"])))


def test_oracle_config_loads_yaml_and_rejects_unknown_keys(tmp_path):
    # Arrange
    good, bad = tmp_path / "good.yaml", tmp_path / "bad.yaml"
    good.write_text("stuck_window_s: 2\ntiers:\n  T1: {label: Stall, points: 7}\n", encoding="utf-8")
    bad.write_text("stuck_window_s: 2\nexecute: rm -rf /\n", encoding="utf-8")

    # Act
    config = load_oracle(good)

    # Assert
    assert config.stuck_window_s == 2
    assert set(config.tiers) == {"T1"} and config.tiers["T1"].points == 7
    assert load_oracle(None) == OracleConfig()
    with pytest.raises(ValueError):
        load_oracle(bad)


# --- scoring ----------------------------------------------------------------------------------


def _trial(root: Path, name: str, errors: list[float], params: str = "nav2: {}\n",
           map_error: float | None | str = "absent") -> Path:
    trial = root / name
    trial.mkdir(parents=True)
    (trial / "nav2.yaml").write_text(params, encoding="utf-8")
    rows = [{"ground_truth": {"sim_time": float(i), "x": 0.0, "y": 0.0},
             "estimated": {"sim_time": float(i), "x": error, "y": 0.0} if error is not None else None}
            for i, error in enumerate(errors)]
    (trial / "telemetry.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    if map_error != "absent":
        (trial / "map-metrics.json").write_text(json.dumps({"map_error": map_error, "coverage": 0.9}))
    return trial


def test_baseline_from_trials_records_position_and_map_thresholds(tmp_path):
    # Arrange
    trials = [_trial(tmp_path, "t1", [0.1, 0.2], map_error=0.4),
              _trial(tmp_path, "t2", [0.3, None], map_error=0.6)]

    # Act
    baseline = baseline_from_trials(Scenario(), trials, durations=[10.0, 12.0])

    # Assert
    assert baseline["distributions"]["position_error_max_m"] == pytest.approx([0.2, 0.3])
    assert baseline["thresholds"]["position_error_m"] == pytest.approx(0.25 + 3 * 0.05)
    assert baseline["distributions"]["map_error"] == [0.4, 0.6]
    assert baseline["thresholds"]["map_error"] == pytest.approx(0.5 + 3 * 0.1)
    assert baseline["completed_trials"] == 2
    assert baseline["identity"]["scenario_hash"] == Scenario().content_hash()


def test_map_threshold_requires_a_map_error_from_every_trial(tmp_path):
    # Arrange
    trials = [_trial(tmp_path, "t1", [0.1], map_error=0.4), _trial(tmp_path, "t2", [0.1], map_error=None)]

    # Act
    baseline = baseline_from_trials(Scenario(), trials, durations=[1.0, 1.0])

    # Assert
    assert "map_error" not in baseline["thresholds"]


def test_baseline_trials_must_share_params_and_have_estimates(tmp_path):
    # Arrange
    first = _trial(tmp_path, "t1", [0.1])
    changed = _trial(tmp_path, "t2", [0.1], params="nav2: {changed: true}\n")
    blind = _trial(tmp_path, "t3", [None])

    # Act / Assert
    with pytest.raises(ValueError, match="parameters changed"):
        baseline_from_trials(Scenario(), [first, changed], durations=[1.0, 1.0])
    with pytest.raises(ValueError, match="No paired pose samples in baseline trial t3"):
        baseline_from_trials(Scenario(), [blind], durations=[1.0])


def test_amcl_profile_adds_localization_map_to_identity(tmp_path):
    # Arrange
    params = tmp_path / "nav2.yaml"
    params.write_text("nav2: {}\n", encoding="utf-8")
    plain = mission_identity(Scenario(), params)
    (tmp_path / "profile.yaml").write_text("navigation_backend: amcl\n", encoding="utf-8")

    # Act
    identity = mission_identity(Scenario(), params)

    # Assert
    assert "localization_map_sha256" not in plain
    assert {"simulation_profile_sha256", "localization_map_sha256"} <= set(identity)
    assert identity["world_mission_sha256"] == plain["world_mission_sha256"]


def _scored_bundle(tmp_path: Path, *, map_metrics: dict | None = None, recoveries=()) -> tuple[Path, dict]:
    bundle = _trial(tmp_path, "bundle", [0.0, 0.0])
    if map_metrics is not None:
        (bundle / "map-metrics.json").write_text(json.dumps(map_metrics))
    (bundle / "metrics.json").write_text(json.dumps({"recoveries": list(recoveries)}))
    baseline = {"schema_version": "1", "identity": mission_identity(Scenario(), bundle / "nav2.yaml"),
                "thresholds": {"position_error_m": 0.5, "map_error": 0.3}, "completed_trials": 1}
    return bundle, baseline


def test_apply_baseline_adds_map_recovery_and_extension_events(tmp_path, monkeypatch):
    # Arrange
    bundle, baseline = _scored_bundle(
        tmp_path, map_metrics={"map_error": 0.5, "coverage": 0.8},
        recoveries=[{"sim_time": 1.0}, {"sim_time": 2.0}, {"sim_time": 3.0}],
    )
    monkeypatch.setattr(oracle_config, "entry_points", _entry_points("glare"))
    config = OracleConfig(extensions=["glare"])
    result = EpisodeResult(status="completed", classification="behavior")

    # Act
    apply_baseline(result, Scenario(), bundle, baseline, config)

    # Assert
    kinds = sorted(event.kind for event in result.events)
    assert kinds == ["glare_detector", "map_error", "recovery_loop"]
    assert result.score == 3 and result.tiers_hit == ["T3", "T1"]
    assert {event.details["tier_label"] for event in result.events} == {"Map error", "Stall or recovery loop"}


def test_apply_baseline_ignores_low_coverage_maps_and_disabled_tiers(tmp_path):
    # Arrange
    bundle, baseline = _scored_bundle(
        tmp_path, map_metrics={"map_error": 0.9, "coverage": 0.1},
        recoveries=[{"sim_time": 1.0}, {"sim_time": 2.0}, {"sim_time": 3.0}],
    )
    config = OracleConfig(tiers={"T4": {"label": "Restricted", "points": 4}})
    result = EpisodeResult(status="completed", classification="behavior")

    # Act
    apply_baseline(result, Scenario(), bundle, baseline, config)

    # Assert
    assert result.events == []
    assert (result.score, result.tiers_hit) == (0, [])


@pytest.mark.parametrize("threshold", [None, -1.0, "0.2", float("inf")])
def test_apply_baseline_requires_a_valid_position_threshold(tmp_path, threshold):
    # Arrange
    bundle, baseline = _scored_bundle(tmp_path)
    baseline["thresholds"]["position_error_m"] = threshold
    result = EpisodeResult(status="completed", classification="behavior")

    # Act / Assert
    with pytest.raises(ValueError, match="valid position-error threshold"):
        apply_baseline(result, Scenario(), bundle, baseline)


def test_apply_baseline_rejects_mismatched_identity(tmp_path):
    # Arrange
    bundle, baseline = _scored_bundle(tmp_path)
    baseline["identity"]["simulation_profile_sha256"] = "unexpected"
    result = EpisodeResult(status="completed", classification="behavior")

    # Act / Assert
    with pytest.raises(ValueError, match="do not match this run"):
        apply_baseline(result, Scenario(), bundle, baseline)


def test_recovery_loop_needs_count_recoveries_inside_window():
    # Arrange
    spread = [{"sim_time": t} for t in (0.0, 20.0, 40.0, 45.0)]
    burst = [{"sim_time": t} for t in (0.0, 20.0, 21.0, 22.0)]

    # Act / Assert
    assert recovery_events(spread, count=3, window=10) == []
    assert [(e.sim_start, e.sim_end) for e in recovery_events(burst, count=3, window=10)] == [(20.0, 22.0)]


# --- results ----------------------------------------------------------------------------------


def _patch_tools(monkeypatch, available: set[str], run):
    monkeypatch.setattr(results, "shutil", SimpleNamespace(which=lambda name: name if name in available else None))
    monkeypatch.setattr(results, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))


def test_runtime_versions_include_ros_packages_and_gazebo(monkeypatch):
    # Arrange
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    monkeypatch.setenv("GZ_VERSION", "harmonic")

    def run(command, **kwargs):
        if command[0] == "dpkg-query":
            return SimpleNamespace(returncode=0, stdout="ros-jazzy-slam-toolbox\t2.8.1\nmalformed\n")
        return SimpleNamespace(returncode=0, stdout="8.6.0\n8.7.0\n")

    _patch_tools(monkeypatch, {"dpkg-query", "gz"}, run)

    # Act
    versions = runtime_versions()

    # Assert
    assert versions["ros"] == "jazzy" and versions["gazebo"] == "harmonic"
    assert versions["ros-jazzy-slam-toolbox"] == "2.8.1"
    assert "malformed" not in versions
    assert versions["gazebo_sim"] == "8.6.0, 8.7.0"


@pytest.mark.parametrize("outcome", ["timeout", "failure"])
def test_runtime_versions_tolerate_broken_gazebo_cli(monkeypatch, outcome):
    # Arrange
    monkeypatch.delenv("ROS_DISTRO", raising=False)

    def run(command, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 5)
        return SimpleNamespace(returncode=1, stdout="")

    _patch_tools(monkeypatch, {"gz"}, run)

    # Act
    versions = runtime_versions()

    # Assert
    assert versions["ros"] == "unavailable"
    assert "gazebo_sim" not in versions


def test_result_bundle_writes_rosbag_reference_and_atomic_result(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(results, "runtime_versions", lambda: {"envfuzz": "test"})
    result = EpisodeResult(status="completed", classification="behavior", sim_duration=3.0)

    # Act
    root = write_result_bundle(tmp_path / "bundle", Scenario(), "<sdf/>", result, logs=None, rosbag="bag-1")

    # Assert
    assert (root / "rosbag").read_text() == "bag-1"
    assert not (root / "logs.txt").exists()
    assert not (root / "result.json.tmp").exists()
    assert EpisodeResult.model_validate_json((root / "result.json").read_text()) == result
    assert json.loads((root / "versions.json").read_text()) == {"envfuzz": "test"}


# --- sensor fixtures --------------------------------------------------------------------------


def test_sensor_fixtures_require_increasing_times_and_unique_ids():
    # Arrange
    rows = [{"time_s": 0.0, "truth_x_m": 0.0, "estimate_x_m": 0.0},
            {"time_s": 0.1, "truth_x_m": 0.0, "estimate_x_m": None}]
    fixture = {"id": "a", "observations": rows, "expected_tiers": []}

    # Act / Assert
    assert SensorFixture.model_validate(fixture).id == "a"
    with pytest.raises(ValueError, match="strictly increasing"):
        SensorFixture.model_validate({**fixture, "observations": rows[::-1]})
    with pytest.raises(ValueError, match="IDs must be unique"):
        FixtureSuite.model_validate({"version": 1, "position_threshold_m": 0.1,
                                     "fixtures": [fixture, fixture]})


# --- fuzzer -----------------------------------------------------------------------------------


def _behaviour(scenario: Scenario) -> EpisodeResult:
    return EpisodeResult(status="completed", classification="behavior")


def test_candidates_skip_object_ids_already_in_the_base():
    # Arrange
    base = Scenario.model_validate({"objects": [
        {"id": "object-1", "shape": "box", "pose": {"x": -3, "y": 3}, "size": {"x": 0.3, "y": 0.3}}]})

    # Act
    generated = next(candidates(seed=4, base=base))

    # Assert
    assert [obj.id for obj in generated.objects] == ["object-1", "object-2"]
    assert generated.seed == 4


def test_fuzz_counts_rejected_candidates_and_stops_at_budget(tmp_path):
    # Arrange
    seed, budget = 3, 4
    expected_rejected, accepted = 0, 0
    for scenario in candidates(seed):
        if validate_scenario(scenario):
            expected_rejected += 1
        else:
            accepted += 1
            if accepted == budget:
                break
    log = tmp_path / "runs" / "results.jsonl"

    # Act
    found, rejected = fuzz(budget, seed, _behaviour, output=log)

    # Assert
    assert len(found) == budget
    assert rejected == expected_rejected
    assert len(log.read_text().splitlines()) == budget


def test_fuzz_budget_edge_cases(tmp_path):
    # Arrange
    impossible = Scenario(world={"min_x": -50.0, "max_x": -49.0, "min_y": -50.0, "max_y": -49.0})

    # Act / Assert
    assert fuzz(0, 1, _behaviour, output=tmp_path / "empty.jsonl") == ([], 0)
    with pytest.raises(ValueError, match="non-negative"):
        fuzz(-1, 1, _behaviour, output=tmp_path / "negative.jsonl")
    with pytest.raises(ValueError, match="after 1000 consecutive placements"):
        fuzz(1, 1, _behaviour, output=tmp_path / "never.jsonl", base=impossible)


def test_fuzz_stops_at_first_infrastructure_failure(tmp_path):
    # Arrange
    def broken(scenario: Scenario) -> EpisodeResult:
        return EpisodeResult(status="launch_failed", classification="infrastructure")

    # Act
    found, _ = fuzz(5, 2, broken, output=tmp_path / "log.jsonl")

    # Assert
    assert [result.status for result in found] == ["launch_failed"]
