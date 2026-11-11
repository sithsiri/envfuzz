"""Submission validation, organizer evaluation and scoreboard ranking without containers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from envfuzz.classroom import check_submission, evaluate_submission, write_scoreboard
from envfuzz.scenario import Scenario, Zone, save_scenario

ZONE = Zone(id="kitchen", polygon=[(-3.0, 1.0), (-1.0, 1.0), (-1.0, 3.0), (-3.0, 3.0)])


def _write_challenge(root: Path, extra: str = "", scenario: Scenario | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    save_scenario(scenario or Scenario(restricted_zones=[ZONE]), root / "fixture.yaml")
    (root / "baseline.json").write_text('{"schema_version": "1"}\n', encoding="utf-8")
    manifest = root / "challenge.yaml"
    manifest.write_text(
        "id: lab\ndifficulty: introductory\nscenario: fixture.yaml\nbaseline: baseline.json\n" + extra,
        encoding="utf-8",
    )
    return manifest


def _clutter(x: float, y: float, object_id: str = "crate") -> dict:
    return {"operator": "add_clutter", "object_id": object_id, "obstacle": {
        "id": object_id, "shape": "box", "pose": {"x": x, "y": y}, "size": {"x": 0.4, "y": 0.4, "z": 0.6},
    }}


def _write_submission(path: Path, edits: list[dict], challenge: str = "lab", team: str = "red") -> Path:
    path.write_text(json.dumps({"challenge": challenge, "team": team, "edits": edits}), encoding="utf-8")
    return path


@pytest.fixture
def manifest(tmp_path) -> Path:
    return _write_challenge(tmp_path / "challenge")


def test_valid_submission_returns_variant_and_digest(manifest, tmp_path):
    # Arrange
    submission = _write_submission(tmp_path / "entry.json", [_clutter(3.0, -3.0)])

    # Act
    challenge, candidate, variant, digest = check_submission(manifest, submission)

    # Assert
    assert challenge.id == "lab" and candidate.team == "red"
    assert [obj.id for obj in variant.objects] == ["crate"]
    assert variant.challenge["require_reachable"] is True
    assert digest == hashlib.sha256(submission.read_bytes()).hexdigest()


def test_submission_for_another_challenge_is_rejected(manifest, tmp_path):
    # Arrange
    submission = _write_submission(tmp_path / "entry.json", [], challenge="other")

    # Act / Assert
    with pytest.raises(ValueError, match="different classroom challenge"):
        check_submission(manifest, submission)


def test_obstacle_touching_restricted_zone_edge_is_rejected(manifest, tmp_path):
    # Arrange: centre is outside the zone, but the footprint radius reaches its edge.
    submission = _write_submission(tmp_path / "entry.json", [_clutter(-0.8, 2.0)])

    # Act / Assert
    with pytest.raises(ValueError, match="crate: intersects forbidden placement region kitchen"):
        check_submission(manifest, submission)


def test_obstacle_inside_restricted_zone_is_rejected_as_invalid_edit(manifest, tmp_path):
    # Arrange
    submission = _write_submission(tmp_path / "entry.json", [_clutter(-2.0, 2.0)])

    # Act / Assert
    with pytest.raises(ValueError, match="inside restricted zone kitchen"):
        check_submission(manifest, submission)


def test_blocking_the_only_route_is_rejected(tmp_path):
    # Arrange: a wall of clutter across a narrow corridor makes the goal unreachable.
    world = {"min_x": -1.0, "max_x": 4.0, "min_y": -0.6, "max_y": 0.6}
    manifest = _write_challenge(tmp_path / "corridor", scenario=Scenario(world=world))
    submission = _write_submission(tmp_path / "entry.json", [
        {"operator": "add_clutter", "object_id": "wall", "obstacle": {
            "id": "wall", "shape": "box", "pose": {"x": 1.0, "y": 0.0},
            "size": {"x": 0.1, "y": 1.1, "z": 0.6}}},
    ])

    # Act / Assert
    with pytest.raises(ValueError, match="no route with robot footprint clearance"):
        check_submission(manifest, submission)


def test_profile_must_match_declared_robot_and_backend(tmp_path):
    # Arrange
    manifest = _write_challenge(tmp_path / "challenge", extra="profile: profile.yaml\n")
    (manifest.parent / "profile.yaml").write_text("navigation_backend: amcl\n", encoding="utf-8")
    submission = _write_submission(tmp_path / "entry.json", [])

    # Act / Assert
    with pytest.raises(ValueError, match="does not match its simulation profile"):
        check_submission(manifest, submission)


@pytest.mark.parametrize("name, body, message", [
    ("entry.yaml", "{}", "one JSON file"),
    ("entry.json", '{"challenge":"lab","team":"' + "x" * 40000 + '","edits":[]}', "byte limit"),
])
def test_submission_file_type_and_size_limits(manifest, tmp_path, name, body, message):
    # Arrange
    submission = tmp_path / name
    submission.write_text(body, encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match=message):
        check_submission(manifest, submission)


class _WorkerScript:
    """Stand-in for the organizer container: returns scripted results and records calls."""

    def __init__(self, outcomes: list[tuple[str, str, float]]) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, scenario, bundle, timeout, watchdog, limits, **kwargs):
        self.calls.append(SimpleNamespace(scenario=scenario, bundle=bundle, timeout=timeout,
                                          watchdog=watchdog, limits=limits, **kwargs))
        status, classification, duration = self.outcomes.pop(0)
        return SimpleNamespace(status=status, classification=classification,
                               sim_duration=duration, tiers_hit=["T1"] if status != "completed" else [])


def test_evaluation_runs_each_repeat_read_only_and_writes_report(tmp_path, monkeypatch):
    # Arrange
    manifest = _write_challenge(tmp_path / "challenge", extra=(
        "oracle: oracle.yaml\nparams: nav2.yaml\nrepeats: 3\ncpus: 1.5\nmemory: 900m\n"))
    for name in ("oracle.yaml", "nav2.yaml"):
        (manifest.parent / name).write_text("{}\n", encoding="utf-8")
    submission = _write_submission(tmp_path / "entry.json", [_clutter(3.0, -3.0)])
    worker = _WorkerScript([("completed", "behavior", 12.0), ("completed", "behavior", 8.0),
                            ("mission_failed", "behavior", 30.0)])
    monkeypatch.setattr("envfuzz.classroom.run_worker", worker)
    output = tmp_path / "evaluation"

    # Act
    report = evaluate_submission(manifest, submission, output, image="envfuzz:test")

    # Assert
    assert report["verified"] is True
    assert (report["successes"], report["valid_runs"], report["expected_runs"]) == (2, 3, 3)
    assert report["median_success_sim_s"] == 10.0
    assert [run["bundle"] for run in report["runs"]] == [
        "repeat-001/bundle", "repeat-002/bundle", "repeat-003/bundle"]
    assert report["runs"][2]["observed_tiers"] == ["T1"]
    assert report["baseline_sha256"] == hashlib.sha256(b'{"schema_version": "1"}\n').hexdigest()
    assert json.loads((output / "evaluation.json").read_text()) == report
    call = worker.calls[0]
    assert call.read_only is True
    assert (call.limits.image, call.limits.cpus, call.limits.memory) == ("envfuzz:test", 1.5, "900m")
    assert call.oracle == manifest.parent / "oracle.yaml"
    assert call.params == manifest.parent / "nav2.yaml"
    assert call.profile is None
    assert [obj.id for obj in call.scenario.objects] == ["crate"]


def test_evaluation_is_unverified_when_any_repeat_is_infrastructure(tmp_path, monkeypatch):
    # Arrange
    manifest = _write_challenge(tmp_path / "challenge", extra="repeats: 2\n")
    submission = _write_submission(tmp_path / "entry.json", [])
    monkeypatch.setattr("envfuzz.classroom.run_worker", _WorkerScript([
        ("watchdog", "infrastructure", 0.0), ("timeout", "behavior", 35.0)]))

    # Act
    report = evaluate_submission(manifest, submission, tmp_path / "out", image="envfuzz:test")

    # Assert
    assert report["verified"] is False
    assert report["successes"] == 0
    assert report["median_success_sim_s"] is None


def test_evaluation_requires_organizer_baseline_and_fresh_output(tmp_path, monkeypatch):
    # Arrange
    manifest = _write_challenge(tmp_path / "challenge")
    submission = _write_submission(tmp_path / "entry.json", [])
    monkeypatch.setattr("envfuzz.classroom.run_worker", _WorkerScript([("completed", "behavior", 1.0)] * 3))
    existing = tmp_path / "existing"
    existing.mkdir()

    # Act / Assert
    with pytest.raises(FileExistsError):
        evaluate_submission(manifest, submission, existing, image="envfuzz:test")
    (manifest.parent / "baseline.json").unlink()
    with pytest.raises(ValueError, match="baseline is missing"):
        evaluate_submission(manifest, submission, tmp_path / "out", image="envfuzz:test")


def _evaluation(path: Path, team: str, successes: int, median: float | None, *,
                verified: bool = True, challenge: str = "lab", digest: str | None = None) -> Path:
    path.write_text(json.dumps({
        "challenge": challenge, "manifest_sha256": "m", "baseline_sha256": "b", "team": team,
        "verified": verified, "successes": successes, "median_success_sim_s": median,
        "submission_sha256": digest or f"sha-{team}",
    }), encoding="utf-8")
    return path


def test_scoreboard_ranks_verified_entries_and_groups_by_challenge(tmp_path):
    # Arrange
    evaluations = [
        _evaluation(tmp_path / "a.json", "slow", 3, 20.0),
        _evaluation(tmp_path / "b.json", "fast", 3, 10.0),
        _evaluation(tmp_path / "c.json", "partial", 2, 5.0),
        _evaluation(tmp_path / "d.json", "none", 3, None),
        _evaluation(tmp_path / "e.json", "cheater", 3, 1.0, verified=False),
        _evaluation(tmp_path / "f.json", "fast", 3, 10.0),  # same submission evaluated twice
        _evaluation(tmp_path / "g.json", "solo", 1, 7.0, challenge="corridor"),
    ]
    output = tmp_path / "boards" / "scoreboard.json"

    # Act
    write_scoreboard(evaluations, output)

    # Assert
    boards = json.loads(output.read_text())
    assert [board["challenge"] for board in boards] == ["corridor", "lab"]
    lab = boards[1]
    assert [entry["team"] for entry in lab["entries"]] == ["fast", "slow", "none", "partial"]
    assert lab["entries"][0]["evaluation"] == str(tmp_path / "f.json")
    assert all(entry["team"] != "cheater" for entry in lab["entries"])
    assert boards[0]["entries"][0]["submission_sha256"] == "sha-solo"
