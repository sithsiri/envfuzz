"""Regression minimization with a deterministic fake simulator (no ROS or Gazebo)."""

import json
from pathlib import Path

import pytest

from envfuzz import minimize
from envfuzz.minimize import minimize_run
from envfuzz.results import EpisodeResult
from envfuzz.scenario import (
    ActorKeyframe,
    Pose,
    Scenario,
    ScenarioObject,
    ScriptedActor,
    Size,
    TimedMove,
    load_scenario,
    validate_scenario,
)

FAILURE_POINT = (1.2, 0.1)


def _box(identifier: str, x: float, y: float) -> ScenarioObject:
    return ScenarioObject(id=identifier, shape="box", pose=Pose(x=x, y=y), size=Size(x=0.4, y=0.4, z=0.6))


def _scenario() -> Scenario:
    """Blocker causes the failure, clutter is removable, mover is timed, walker is an actor."""
    return Scenario(
        version="2",
        challenge={"layout": "corridor"},
        objects=[
            _box("blocker", 1.0, -2.0),
            _box("clutter", -2.0, -2.0),
            _box("mover", -2.0, 2.0),
            ScenarioObject(id="walker", shape="cylinder", pose=Pose(x=3, y=2), radius=0.2, height=1.5),
        ],
        events=[TimedMove(at=3, object_id="mover", pose=Pose(x=-2, y=3))],
        actors=[ScriptedActor(object_id="walker", keyframes=[
            ActorKeyframe(at=3, pose=Pose(x=3, y=2)), ActorKeyframe(at=4, pose=Pose(x=3, y=3)),
        ])],
    )


def _write_outcome(bundle: Path, result: EpisodeResult, terminal: tuple[float, float]) -> None:
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    row = {"ground_truth": {"sim_time": 4, "x": terminal[0], "y": terminal[1]}, "estimated": None}
    (bundle / "telemetry.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def _original_bundle(root: Path, scenario: Scenario, result: EpisodeResult) -> Path:
    _write_outcome(root, result, FAILURE_POINT)
    (root / "scenario.json").write_text(json.dumps(scenario.canonical_dict()), encoding="utf-8")
    (root / "run.json").write_text(json.dumps({"timeout": 40, "watchdog": 90}), encoding="utf-8")
    (root / "nav2.yaml").write_text("nav2: {}", encoding="utf-8")
    return root


def _blocker_simulator(calls: list):
    """Reproduce the failure exactly when the blocker object is still present."""
    def run(scenario, trial, timeout, watchdog, *options):
        calls.append((trial, timeout, watchdog, options, [obj.id for obj in scenario.objects]))
        if any(obj.id == "blocker" for obj in scenario.objects):
            _write_outcome(trial, EpisodeResult(status="mission_failed", classification="behavior"), FAILURE_POINT)
        else:
            _write_outcome(trial, EpisodeResult(status="completed", classification="behavior"), (2, 0))
        return EpisodeResult.model_validate_json((trial / "result.json").read_text(encoding="utf-8"))
    return run


def test_fixture_scenario_is_valid():
    assert validate_scenario(_scenario()) == []


def test_minimization_keeps_only_objects_needed_to_reproduce(tmp_path, monkeypatch):
    bundle = _original_bundle(tmp_path / "original", _scenario(),
                              EpisodeResult(status="mission_failed", classification="behavior"))
    calls = []
    monkeypatch.setattr(minimize, "run_gazebo", _blocker_simulator(calls))
    output = tmp_path / "minimized"

    report = minimize_run(bundle, output, repeats=2, required_rate=1.0)

    assert report["signature"] == ("corridor", "mission_failed", False, (1, 0))
    assert (report["original_objects"], report["remaining_objects"]) == (4, 3)
    assert [attempt["candidate"] for attempt in report["candidates"]] == [
        "original", "candidate-001", "candidate-002",
    ]
    assert [attempt["rate"] for attempt in report["candidates"]] == [1.0, 0.0, 1.0]
    assert report["selected"]["candidate"] == "candidate-002"
    assert report["selected"]["bundles"] == ["candidate-002/repeat-001", "candidate-002/repeat-002"]
    assert report["original_bundle"] == str(bundle.resolve())
    minimized = load_scenario(output / "scenario.yaml")
    assert [obj.id for obj in minimized.objects] == ["blocker", "mover", "walker"]
    assert json.loads((output / "minimization.json").read_text())["remaining_objects"] == 3
    assert {call[1:3] for call in calls} == {(40, 90)}
    assert calls[0][3] == (bundle / "nav2.yaml", None, None, None)
    assert len(calls) == 6


def test_minimization_rejects_runs_without_a_regression(tmp_path, monkeypatch):
    bundle = _original_bundle(tmp_path / "original", _scenario(),
                              EpisodeResult(status="completed", classification="behavior"))
    monkeypatch.setattr(minimize, "run_gazebo", _blocker_simulator([]))

    with pytest.raises(ValueError, match="no recorded navigation or mapping regression"):
        minimize_run(bundle, tmp_path / "minimized", repeats=1, required_rate=1.0)

    assert not (tmp_path / "minimized").exists()


def test_minimization_stops_when_original_does_not_reproduce(tmp_path, monkeypatch):
    bundle = _original_bundle(tmp_path / "original", _scenario(),
                              EpisodeResult(status="mission_failed", classification="behavior"))

    def flaky(scenario, trial, timeout, watchdog, *options):
        result = EpisodeResult(status="watchdog", classification="infrastructure")
        _write_outcome(trial, result, FAILURE_POINT)
        return result

    monkeypatch.setattr(minimize, "run_gazebo", flaky)

    with pytest.raises(ValueError, match="did not reproduce at the required rate"):
        minimize_run(bundle, tmp_path / "minimized", repeats=2, required_rate=0.5)

    assert not (tmp_path / "minimized" / "minimization.json").exists()


def test_minimization_refuses_to_overwrite_existing_output(tmp_path, monkeypatch):
    bundle = _original_bundle(tmp_path / "original", _scenario(),
                              EpisodeResult(status="mission_failed", classification="behavior"))
    monkeypatch.setattr(minimize, "run_gazebo", _blocker_simulator([]))
    output = tmp_path / "minimized"
    output.mkdir()

    with pytest.raises(FileExistsError):
        minimize_run(bundle, output, repeats=1, required_rate=1.0)
