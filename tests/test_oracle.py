import json

from envfuzz.oracle import pose_error_events, restricted_events, score_events, stuck_events
from envfuzz.results import EpisodeResult, PoseSample
from envfuzz.scenario import Scenario, Zone
from envfuzz.scoring import apply_baseline, baseline_from_trials


def samples(points):
    return [PoseSample(sim_time=t, x=x, y=y) for t, x, y in points]


def test_stuck_and_pose_error_and_zone_events():
    trace = samples([(0, 0, 0), (1, 0.01, 0), (2, 0.01, 0)])
    assert stuck_events(trace, 0.1, 2)
    assert pose_error_events(trace, samples([(0, 1, 0), (1, 1, 0), (2, 1, 0)]), 0.5, 1)
    assert restricted_events(samples([(0, 0, 0), (1, 0.5, 0), (2, 2, 0)]),
                             [Zone(id="no-go", polygon=[(0, -1), (1, -1), (1, 1), (0, 1)])])
    assert score_events(stuck_events(trace, 0.1, 2))[0] == 1


def test_baseline_uses_paired_pose_error_and_scores_a_live_trace(tmp_path):
    scenario = Scenario()
    (tmp_path / "nav2.yaml").write_text("nav2: {}")
    telemetry = tmp_path / "telemetry.jsonl"

    def write_trace(error):
        rows = []
        for index in range(11):
            sample = {"sim_time": index / 10, "x": 0, "y": 0}
            rows.append(json.dumps({
                "ground_truth": sample, "estimated": {**sample, "x": error},
            }))
        telemetry.write_text("\n".join(rows) + "\n")

    write_trace(0.02)
    baseline = baseline_from_trials(scenario, [tmp_path], [5.0])
    assert baseline["distributions"]["position_error_max_m"] == [0.02]
    assert baseline["thresholds"]["position_error_m"] == 0.1
    write_trace(0.5)
    result = EpisodeResult(status="completed", classification="behavior")
    apply_baseline(result, scenario, tmp_path, baseline)
    assert result.tiers_hit == ["T2"]
    assert result.score == 2
    assert result.baseline_sha256


def test_pose_error_does_not_bridge_missing_samples_and_footprint_reaches_zone():
    truth = samples([(0, 0, 0), (0.1, 0, 0), (0.2, 0, 0), (0.3, 0, 0)])
    estimates = samples([(0, 1, 0), (0.1, 1, 0), (0.3, 1, 0)])
    assert not pose_error_events(truth, estimates, 0.5, 0.25)
    zone = Zone(id="edge", polygon=[(0.1, -1), (1, -1), (1, 1), (0.1, 1)])
    assert restricted_events(truth, [zone], radius=0.22)
