"""CLI wiring for analyze, minimize, baseline, campaign, campaign-worker and fuzz (fake simulators)."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from envfuzz import cli
from envfuzz.cli import app
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Pose, Scenario, save_scenario
from envfuzz.workers import WorkerLimits

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()


def _plain(result) -> str:
    """Collapse typer's boxed error panel into a single line of text."""
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", result.output).split())


def _invoke(*args):
    return runner.invoke(app, [str(arg) for arg in args])


def _raise(error):
    def failing(*args, **kwargs):
        raise error
    return failing


# analyze / minimize


def test_analyze_writes_default_report_without_replays(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "analyze_campaign", lambda root, target: calls.append((root, target)) or {
        "shared_budget": 4})
    monkeypatch.setattr(cli, "replay_corpus", _raise(AssertionError("replay must not run")))

    result = _invoke("analyze", tmp_path)

    assert result.exit_code == 0
    assert result.output == f"4 episodes per strategy. {tmp_path / 'analysis.json'}\n"
    assert calls == [(tmp_path, tmp_path / "analysis.json")]


def test_analyze_replays_corpus_with_requested_workers(tmp_path, monkeypatch):
    report = {"shared_budget": 2, "corpus": []}
    replays = []
    monkeypatch.setattr(cli, "analyze_campaign", lambda root, target: report)
    monkeypatch.setattr(cli, "replay_corpus", lambda *args: replays.append(args))
    output = tmp_path / "out.json"

    result = _invoke("analyze", tmp_path, "--output", output, "--replays", "3", "--workers", "2",
                     "--image", "envfuzz:test")

    assert result.exit_code == 0
    assert replays == [(tmp_path, output, report, 3, 2, "envfuzz:test")]


def test_analyze_turns_index_errors_into_bad_parameter(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "analyze_campaign", _raise(OSError("plan.json missing")))

    result = _invoke("analyze", tmp_path)

    assert result.exit_code == 2
    assert "plan.json missing" in _plain(result)


def test_minimize_reports_remaining_objects(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "minimize_run", lambda *args: calls.append(args) or {"remaining_objects": 2})
    output = tmp_path / "minimized"

    result = _invoke("minimize", tmp_path, "--output", output, "--repeats", "4", "--minimum-rate", "0.5")

    assert result.exit_code == 0
    assert result.output == f"2 objects. {output / 'minimization.json'}\n"
    assert calls == [(tmp_path, output, 4, 0.5)]


def test_minimize_turns_reproduction_failure_into_bad_parameter(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "minimize_run", _raise(ValueError("Original run did not reproduce")))

    result = _invoke("minimize", tmp_path, "--output", tmp_path / "minimized")

    assert result.exit_code == 2
    assert "Original run did not reproduce" in _plain(result)


# baseline (exercises the retry helper)


def _baseline_simulator(statuses: list[str], calls: list, with_estimates: bool = True):
    """Write a minimal Gazebo-like bundle; statuses are consumed one per launch."""
    def run(scenario, bundle, timeout, watchdog, params_file, baseline_file=None, oracle_file=None,
            profile_file=None):
        calls.append(bundle.name)
        status = statuses.pop(0) if statuses else "completed"
        bundle.mkdir(parents=True)
        (bundle / "nav2.yaml").write_text("nav2: {}", encoding="utf-8")
        rows = [{"ground_truth": {"sim_time": t, "x": t, "y": 0},
                 "estimated": {"sim_time": t, "x": t + 0.05, "y": 0} if with_estimates else None}
                for t in range(3)]
        (bundle / "telemetry.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows),
                                                encoding="utf-8")
        infrastructure = status in {"watchdog", "simulator_failed"}
        return EpisodeResult(status=status, classification="infrastructure" if infrastructure else "behavior",
                             sim_duration=10.0, reason=f"{status} reason" if infrastructure else None)
    return run


def test_baseline_retries_infrastructure_failures_and_calibrates_thresholds(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_gazebo", _baseline_simulator(["watchdog"], calls))
    output = tmp_path / "baseline"

    result = _invoke("baseline", EXAMPLES / "scenario.yaml", "--n", "2", "--output", output)

    assert result.exit_code == 0, result.output
    assert calls == ["trial-0001", "trial-0001-retry-1", "trial-0002"]
    assert "watchdog" in result.output and "Retrying." in result.output
    data = json.loads((output / "baseline.json").read_text())
    assert data["completed_trials"] == 2
    assert data["distributions"]["position_error_max_m"] == pytest.approx([0.05, 0.05])
    assert data["thresholds"]["position_error_m"] == 0.1
    assert result.output.endswith(f"{output / 'baseline.json'}\n")


def test_baseline_gives_up_after_three_infrastructure_attempts(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_gazebo", _baseline_simulator(["simulator_failed"] * 3, calls))
    output = tmp_path / "baseline"

    result = _invoke("baseline", "--n", "1", "--output", output)

    assert result.exit_code == 2
    assert calls == ["trial-0001", "trial-0001-retry-1", "trial-0001-retry-2"]
    assert "simulator_failed reason" in result.output
    assert not (output / "baseline.json").exists()


def test_baseline_refuses_existing_output_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_gazebo", _raise(AssertionError("must not launch")))

    result = _invoke("baseline", "--output", tmp_path)

    assert result.exit_code == 2
    assert "Choose a new output directory" in _plain(result)


def test_baseline_rejects_trials_without_paired_pose_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_gazebo", _baseline_simulator([], [], with_estimates=False))

    result = _invoke("baseline", "--n", "1", "--output", tmp_path / "baseline")

    assert result.exit_code == 2
    assert "No paired pose samples in baseline trial trial-0001" in _plain(result)


# campaign / campaign-worker


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "campaign.yaml"
    path.write_text("layouts: [corridor]\nmethods: [uniform]\nseeds: [1]\nepisodes_per_cell: 1\n",
                    encoding="utf-8")
    return path


@pytest.fixture
def fake_campaign(monkeypatch):
    calls = {"sequential": [], "parallel": [], "worker": []}
    monkeypatch.setattr(cli, "prepare_campaign", lambda path, output: ("config", ["case-a", "case-b"]))
    monkeypatch.setattr(cli, "join_campaign", lambda path, output: ("config", ["case-a", "case-b"]))
    monkeypatch.setattr(cli, "run_campaign", lambda *args: calls["sequential"].append(args) or True)
    monkeypatch.setattr(cli, "run_parallel_campaign", lambda *args: calls["parallel"].append(args) or True)
    monkeypatch.setattr(cli, "run_durable_worker",
                        lambda *args, **kwargs: calls["worker"].append((args, kwargs)) or 5)
    return calls


def test_campaign_plan_only_does_not_run_cases(tmp_path, manifest, fake_campaign):
    output = tmp_path / "runs"

    result = _invoke("campaign", manifest, "--output", output, "--plan-only")

    assert result.exit_code == 0
    assert result.output == f"2 cases in {output / 'plan.json'}\n"
    assert fake_campaign["sequential"] == fake_campaign["parallel"] == []


def test_campaign_runs_sequentially_without_workers(tmp_path, manifest, fake_campaign):
    output = tmp_path / "runs"

    result = _invoke("campaign", manifest, "--output", output)

    assert result.exit_code == 0
    assert result.output.endswith(f"{output / 'results.csv'}\n")
    assert fake_campaign["sequential"] == [("config", ["case-a", "case-b"], output)]


def test_campaign_runs_in_parallel_with_worker_limits(tmp_path, manifest, fake_campaign):
    output = tmp_path / "runs"

    result = _invoke("campaign", manifest, "--output", output, "--workers", "3", "--image", "img:2",
                     "--worker-cpus", "1.5", "--worker-memory", "2g", "--retries", "4")

    assert result.exit_code == 0
    assert fake_campaign["parallel"] == [
        ("config", ["case-a", "case-b"], output, 3, WorkerLimits("img:2", 1.5, "2g"), 4),
    ]


def test_campaign_pauses_with_nonzero_exit_when_incomplete(tmp_path, manifest, fake_campaign, monkeypatch):
    monkeypatch.setattr(cli, "run_campaign", lambda *args: False)

    result = _invoke("campaign", manifest, "--output", tmp_path / "runs")

    assert result.exit_code == 2
    assert "Campaign paused after an incomplete or infrastructure run" in result.output


@pytest.mark.parametrize("target", ["prepare_campaign", "run_campaign"])
def test_campaign_turns_value_errors_into_bad_parameter(tmp_path, manifest, fake_campaign, monkeypatch, target):
    monkeypatch.setattr(cli, target, _raise(ValueError("different campaign manifest")))

    result = _invoke("campaign", manifest, "--output", tmp_path / "runs")

    assert result.exit_code == 2
    assert "different campaign manifest" in _plain(result)


def test_campaign_worker_reports_committed_results(tmp_path, manifest, fake_campaign):
    output = tmp_path / "shared"
    output.mkdir()

    result = _invoke("campaign-worker", manifest, "--output", output, "--worker-id", "w1",
                     "--lease-s", "30", "--retries", "1")

    assert result.exit_code == 0
    assert result.output == f"5 authoritative results committed in {output}\n"
    (args, kwargs), = fake_campaign["worker"]
    assert args == ("config", ["case-a", "case-b"], output, "w1", WorkerLimits("envfuzz:jazzy", 2.0, "1536m"))
    assert kwargs == {"lease_s": 30.0, "retries": 1, "wait": True, "poll_s": 10.0,
                      "max_consecutive_failures": 3}


def test_campaign_worker_turns_errors_into_bad_parameter(tmp_path, manifest, fake_campaign, monkeypatch):
    output = tmp_path / "shared"
    output.mkdir()
    monkeypatch.setattr(cli, "run_durable_worker", _raise(OSError("lease directory unavailable")))

    result = _invoke("campaign-worker", manifest, "--output", output, "--worker-id", "w1")

    assert result.exit_code == 2
    assert "lease directory unavailable" in _plain(result)


# fuzz


def test_smoke_fuzz_records_every_completed_candidate(tmp_path):
    output = tmp_path / "results.jsonl"

    result = _invoke("fuzz", "--budget", "2", "--seed", "7", "--output", output, "--smoke")

    assert result.exit_code == 0, result.output
    assert re.search(r"completed=2 rejected=\d+\n$", result.output)
    lines = output.read_text().splitlines()
    assert [json.loads(line)["status"] for line in lines] == ["completed", "completed"]


def test_live_fuzz_requires_a_baseline(tmp_path):
    result = _invoke("fuzz", "--budget", "1", "--output", tmp_path / "results.jsonl")

    assert result.exit_code == 2
    assert "Live fuzzing requires --baseline" in _plain(result)


def test_live_fuzz_stops_after_repeated_infrastructure_failure(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    calls = []

    def fake_gazebo(scenario, bundle, timeout, watchdog, params_file, baseline_file=None, profile_file=None):
        calls.append((bundle, timeout, baseline_file))
        return EpisodeResult(status="simulator_failed", classification="infrastructure", reason="gz crashed")

    monkeypatch.setattr(cli, "run_gazebo", fake_gazebo)
    output = tmp_path / "fuzz" / "results.jsonl"

    result = _invoke("fuzz", "--budget", "2", "--seed", "3", "--output", output, "--baseline", baseline,
                     "--timeout", "9")

    assert result.exit_code == 2
    run_dir = Path(re.search(r"Episode bundles: (\S+)", result.output).group(1))
    assert run_dir.parent == output.parent and run_dir.name.startswith("fuzz-3-")
    assert [call[0].name for call in calls] == ["case-0001", "case-0001-retry-1", "case-0001-retry-2"]
    assert {call[1:] for call in calls} == {(9.0, baseline)}
    assert "completed=0" in result.output and "gz crashed" in result.output
    assert json.loads(output.read_text())["status"] == "simulator_failed"


def test_live_fuzz_ranks_completed_results_by_score(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    scores = iter([3, 8])
    monkeypatch.setattr(cli, "run_gazebo", lambda *args, **kwargs: EpisodeResult(
        status="completed", classification="behavior", score=next(scores), tiers_hit=["T2"]))

    result = _invoke("fuzz", "--budget", "2", "--output", tmp_path / "results.jsonl", "--baseline", baseline)

    assert result.exit_code == 0, result.output
    ranking = [line for line in result.output.splitlines() if "score=" in line]
    assert ranking == ["2: score=8 tiers=T2", "1: score=3 tiers=T2"]
    assert result.output.endswith("completed=2 rejected=0\n")


def test_fuzz_reports_when_no_candidate_fits_the_world(tmp_path):
    scenario = tmp_path / "tiny.yaml"
    save_scenario(Scenario(world={"min_x": -0.6, "max_x": 0.6, "min_y": -0.6, "max_y": 0.6},
                           goals=[Pose(x=0.2, y=0)]), scenario)

    result = _invoke("fuzz", "--budget", "1", "--smoke", "--scenario", scenario,
                     "--output", tmp_path / "results.jsonl")

    assert result.exit_code == 2
    assert "No valid candidate found after 1000 consecutive placements" in _plain(result)
