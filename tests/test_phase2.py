import json
import math
import os
from pathlib import Path
from threading import Barrier

import pytest

from envfuzz.campaign import prepare_campaign, run_parallel_campaign
from envfuzz.campaign_index import render_campaign_report
from envfuzz.maps import Grid, compare_grids, export_navigation_map
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Pose, Scenario, compile_sdf, validate_scenario
from envfuzz.scoring import recovery_events
from envfuzz.variants import EditBudget, apply_edits, seeded_clutter_edit
from envfuzz.workers import WorkerLimits


def test_aligned_map_ignores_unknown_and_rotates_cells():
    reference = Grid(width=2, height=2, resolution=1, origin=Pose(x=0, y=0), data=[100, 0, 0, -1])
    estimate = Grid(width=2, height=2, resolution=1, origin=Pose(x=2, y=0, yaw=math.pi / 2),
                    data=[0, -1, 100, 0])
    metrics = compare_grids(reference, estimate)
    assert metrics["occupied_iou"] == 1
    assert metrics["coverage"] == 1
    estimate.data[3] = -1
    assert compare_grids(reference, estimate)["coverage"] == pytest.approx(2 / 3)


def test_navigation_map_export_is_deterministic_and_self_contained(tmp_path):
    scenario = Scenario()
    metadata = export_navigation_map(scenario, tmp_path)
    image = tmp_path / "localization-map.pgm"
    assert metadata.name == "localization-map.yaml"
    assert "image: localization-map.pgm" in metadata.read_text()
    first = image.read_bytes()
    assert first.startswith(b"P5\n")
    assert export_navigation_map(scenario, tmp_path).read_bytes() == metadata.read_bytes()
    assert image.read_bytes() == first


def test_recovery_evidence_uses_simulation_window():
    recoveries = [{"sim_time": time, "count": index} for index, time in enumerate((1, 8, 9, 10), 1)]
    event, = recovery_events(recoveries, 3, 3)
    assert (event.sim_start, event.sim_end, event.measured_value) == (8, 10, 3)


def test_seeded_clutter_accepted_variants_always_validate():
    base = Scenario()
    accepted = 0
    for seed in range(100):
        edit = seeded_clutter_edit(base, seed, "chair")
        assert edit == seeded_clutter_edit(base, seed, "chair")
        try:
            variant = apply_edits(base, [edit], EditBudget())
        except ValueError:
            continue
        accepted += 1
        assert not validate_scenario(variant)
    assert accepted > 50
    with pytest.raises(ValueError, match="Edit count"):
        apply_edits(base, [seeded_clutter_edit(base, 5, "chair")], EditBudget(max_edits=0))


def test_legacy_scenario_serialization_and_versioned_timeline():
    assert "events" not in Scenario().canonical_dict()
    with pytest.raises(ValueError, match="requires scenario version 2"):
        Scenario(events=[])
    from pathlib import Path
    from envfuzz.scenario import load_scenario

    scenario = load_scenario(Path(__file__).parents[1] / "examples" / "timed-move.yaml")
    sdf = compile_sdf(scenario)
    assert "libenvfuzz_timeline.so" in sdf
    assert "<nanoseconds>3000000000</nanoseconds>" in sdf


def test_parallel_resume_retries_infrastructure_and_indexes_attempts(tmp_path, monkeypatch):
    path = tmp_path / "manifest.yaml"
    path.write_text("layouts: [corridor]\nmethods: [uniform]\nseeds: [42]\nepisodes_per_cell: 2\n")
    root = tmp_path / "output"
    manifest, cases = prepare_campaign(path, root)
    calls = []
    domains = []
    ready = Barrier(2, timeout=5)

    def fake_worker(scenario, bundle, timeout, watchdog, limits, *, domain_id):
        calls.append(bundle)
        if bundle.parent.name == "attempt-000001":
            domains.append(domain_id)
            ready.wait()
        bundle.mkdir(parents=True)
        fails = bundle.parent.name == "attempt-000001" and bundle.parent.parent.name == "case-000001"
        result = EpisodeResult(status="watchdog", classification="infrastructure") if fails else EpisodeResult(
            status="completed", classification="behavior", score=2, tiers_hit=["T2"],
        )
        (bundle / "result.json").write_text(result.model_dump_json())
        return result

    monkeypatch.setattr("envfuzz.campaign.run_worker", fake_worker)
    assert run_parallel_campaign(manifest, cases, root, 2, WorkerLimits())
    assert len(calls) == 3
    assert set(domains) == {10, 11}
    assert run_parallel_campaign(manifest, cases, root, 2, WorkerLimits())
    assert len(calls) == 3
    report = root / "filtered.html"
    render_campaign_report(root, report, tier="T2", strategy="uniform", minimum_score=2)
    assert report.read_text().count(">Result</a>") == 2
    assert "infrastructure: 1" in report.read_text()


def test_classroom_rejects_oversized_and_executable_submissions(tmp_path):
    from envfuzz.classroom import check_submission
    from envfuzz.scenario import save_scenario

    save_scenario(Scenario(), tmp_path / "fixture.yaml")
    challenge = tmp_path / "challenge.yaml"
    challenge.write_text(
        "id: test\ndifficulty: introductory\nscenario: fixture.yaml\nbaseline: baseline.json\n"
        "max_submission_bytes: 256\nbudget: {allowed: [add_clutter], max_edits: 0}\n"
    )
    submission = tmp_path / "attempt.json"
    submission.write_text(" " * 257)
    with pytest.raises(ValueError, match="byte limit"):
        check_submission(challenge, submission)
    script = tmp_path / "attempt.sh"
    script.write_text("exit 0")
    with pytest.raises(ValueError, match="one JSON file"):
        check_submission(challenge, script)
    submission.write_text(json.dumps({"challenge": "test", "team": "test", "edits": [], "score": 10}))
    with pytest.raises(ValueError, match="Extra inputs"):
        check_submission(challenge, submission)
    submission.write_text(json.dumps({"challenge": "test", "team": "test", "edits": []}))
    _, _, scenario, _ = check_submission(challenge, submission)
    assert scenario.challenge["require_reachable"]


def test_scored_map_uses_configuration_and_coverage(tmp_path):
    from envfuzz.oracle_config import OracleConfig, Tier
    from envfuzz.scoring import apply_baseline, mission_identity

    scenario = Scenario()
    params = tmp_path / "nav2.yaml"
    params.write_text("nav2: {}")
    (tmp_path / "telemetry.jsonl").write_text(json.dumps({
        "ground_truth": {"sim_time": 1, "x": 0, "y": 0}, "estimated": None,
    }) + "\n")
    comparison = tmp_path / "map-metrics.json"
    comparison.write_text('{"map_error": 0.7, "coverage": 0.9}')
    baseline = {"schema_version": "1", "identity": mission_identity(scenario, params),
                "thresholds": {"position_error_m": .1, "map_error": .3}}
    oracle = OracleConfig(tiers={"T3": Tier(label="Map drift", points=8)})
    result = EpisodeResult(status="completed", classification="behavior")
    apply_baseline(result, scenario, tmp_path, baseline, oracle)
    assert result.score == 8
    assert result.events[0].details["tier_label"] == "Map drift"
    comparison.write_text('{"map_error": 0.7, "coverage": 0.01}')
    apply_baseline(result, scenario, tmp_path, baseline, oracle)
    assert result.score == 0


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ENVFUZZ_GAZEBO_TEST") != "1", reason="requires ROS 2 and Gazebo")
def test_timed_move_updates_lidar_at_simulation_time(tmp_path):
    from envfuzz.gazebo import run_gazebo
    from envfuzz.scenario import load_scenario

    scenario = load_scenario(Path(__file__).parents[1] / "examples" / "timed-move.yaml")
    bundle = tmp_path / "timed"
    result = run_gazebo(scenario, bundle, timeout=30, watchdog=90)
    assert result.classification == "behavior", result.reason
    event, = json.loads((bundle / "events-applied.json").read_text())
    assert event["applied_sim_time"] == pytest.approx(3, abs=.001)
    scans = [json.loads(line) for line in (bundle / "scans.jsonl").read_text().splitlines()]
    before = [scan["ranges"] for scan in scans if scan["sim_time"] < 3][-1]
    after = [scan["ranges"] for scan in scans if scan["sim_time"] > 3][0]
    assert before != after
    assert (bundle / "map-metrics.json").is_file()
