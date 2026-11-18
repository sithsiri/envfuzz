"""CLI wiring for scenario, classroom, run, replay, report and show commands (no ROS or Gazebo)."""

import json
import re
import runpy
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from envfuzz import cli
from envfuzz.cli import app
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Pose, Scenario, save_scenario

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
SCENARIO = EXAMPLES / "scenario.yaml"
runner = CliRunner()


def _plain(result) -> str:
    """Collapse typer's boxed error panel into a single line of text."""
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", result.output).split())


def _invoke(*args):
    return runner.invoke(app, [str(arg) for arg in args])


# scenario / compile / surface-probe / doctor


def test_scenario_validate_prints_schema_without_reading_the_file(tmp_path):
    result = _invoke("scenario", "validate", tmp_path / "unused.yaml", "--schema")

    assert result.exit_code == 0
    assert "objects" in json.loads(result.output)["properties"]


def test_scenario_validate_accepts_valid_file_and_prints_content_hash():
    result = _invoke("scenario", "validate", SCENARIO)

    assert result.exit_code == 0
    assert re.fullmatch(r"valid scenario: [0-9a-f]{64}\n", result.output)


def test_scenario_validate_reports_rule_violations(tmp_path):
    path = tmp_path / "scenario.yaml"
    save_scenario(Scenario(goals=[Pose(x=4.9, y=0)]), path)

    result = _invoke("scenario", "validate", path)

    assert result.exit_code == 2
    assert "goal 1: violates wall clearance" in _plain(result)


@pytest.mark.parametrize("content, message", [
    ("objects: 5\n", "Input should be a valid list"),
    ("- not a mapping\n", "scenario root must be a mapping"),
])
def test_scenario_validate_reports_schema_errors(tmp_path, content, message):
    path = tmp_path / "scenario.yaml"
    path.write_text(content, encoding="utf-8")

    result = _invoke("scenario", "validate", path)

    assert result.exit_code == 2
    assert message in _plain(result)


def test_compile_writes_sdf_world_to_requested_path(tmp_path):
    output = tmp_path / "world.sdf"

    result = _invoke("compile", SCENARIO, "--output", output)

    assert result.exit_code == 0
    assert result.output == f"{output}\n"
    assert ET.parse(output).getroot().tag == "sdf"


@pytest.mark.parametrize("scenario_file, message", [
    (EXAMPLES / "timed-move.yaml", "A surface probe requires a static fixture"),
    (Path("missing.yaml"), "No such file or directory"),
])
def test_surface_probe_rejects_dynamic_or_missing_fixtures(scenario_file, message):
    result = _invoke("surface-probe", scenario_file)

    assert result.exit_code == 2
    assert message in _plain(result)


def test_surface_probe_rejects_invalid_fixture(tmp_path):
    path = tmp_path / "fixture.yaml"
    save_scenario(Scenario(start=Pose(x=-4.9, y=0)), path)

    result = _invoke("surface-probe", path)

    assert result.exit_code == 2
    assert "start: violates wall clearance" in _plain(result)


def test_doctor_prints_runtime_versions_as_json(monkeypatch):
    monkeypatch.delenv("ROS_DISTRO", raising=False)

    result = _invoke("doctor")

    assert result.exit_code == 0
    versions = json.loads(result.output)
    assert versions["envfuzz"] == "0.1.0"
    assert versions["ros"] == "unavailable"


def test_module_entry_point_runs_the_cli(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["envfuzz", "doctor"])
    monkeypatch.delitem(sys.modules, "envfuzz.__main__", raising=False)

    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("envfuzz", run_name="__main__")

    assert stopped.value.code == 0
    assert json.loads(capsys.readouterr().out)["envfuzz"] == "0.1.0"


# classroom


def _challenge(tmp_path: Path) -> Path:
    save_scenario(Scenario(), tmp_path / "fixture.yaml")
    challenge = tmp_path / "challenge.yaml"
    challenge.write_text(
        "id: test\ndifficulty: introductory\nscenario: fixture.yaml\nbaseline: baseline.json\n"
        "budget: {allowed: [add_clutter], max_edits: 0}\n", encoding="utf-8",
    )
    return challenge


def test_classroom_check_accepts_valid_submission(tmp_path):
    submission = tmp_path / "attempt.json"
    submission.write_text(json.dumps({"challenge": "test", "team": "blue", "edits": []}), encoding="utf-8")

    result = _invoke("classroom", "check", _challenge(tmp_path), submission)

    assert result.exit_code == 0
    assert re.fullmatch(r"Valid submission\. Scenario [0-9a-f]{64}\n", result.output)


def test_classroom_check_rejects_submission_for_other_challenge(tmp_path):
    submission = tmp_path / "attempt.json"
    submission.write_text(json.dumps({"challenge": "other", "team": "blue", "edits": []}), encoding="utf-8")

    result = _invoke("classroom", "check", _challenge(tmp_path), submission)

    assert result.exit_code == 2
    assert "Invalid value" in _plain(result)


def test_classroom_sensor_fixture_check_rejects_malformed_suite(tmp_path):
    path = tmp_path / "fixtures.json"
    path.write_text("{not json", encoding="utf-8")

    result = _invoke("classroom", "check-sensor-fixtures", path)

    assert result.exit_code == 2
    assert "Invalid JSON" in _plain(result)


def test_classroom_evaluate_reports_verified_successes(tmp_path, monkeypatch):
    calls = []

    def fake_evaluate(manifest, submission, output, image):
        calls.append((manifest, submission, output, image))
        return {"successes": 2, "valid_runs": 3, "verified": True}

    monkeypatch.setattr(cli, "evaluate_submission", fake_evaluate)
    output = tmp_path / "evaluation"

    result = _invoke("classroom", "evaluate", "m.yaml", "s.json", "--output", output, "--image", "img:1")

    assert result.exit_code == 0
    assert result.output == f"2 successes across 3 valid runs. {output / 'evaluation.json'}\n"
    assert calls == [(Path("m.yaml"), Path("s.json"), output, "img:1")]


def test_classroom_evaluate_exits_nonzero_when_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "evaluate_submission",
                        lambda *args: {"successes": 0, "valid_runs": 1, "verified": False})

    result = _invoke("classroom", "evaluate", "m.yaml", "s.json", "--output", tmp_path / "evaluation")

    assert result.exit_code == 2
    assert "0 successes across 1 valid runs" in result.output


def test_classroom_evaluate_turns_errors_into_bad_parameter(tmp_path, monkeypatch):
    def failing(*args):
        raise ValueError("The organizer baseline is missing")

    monkeypatch.setattr(cli, "evaluate_submission", failing)

    result = _invoke("classroom", "evaluate", "m.yaml", "s.json", "--output", tmp_path / "evaluation")

    assert result.exit_code == 2
    assert "The organizer baseline is missing" in _plain(result)


def test_classroom_scoreboard_ranks_verified_evaluations(tmp_path):
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({
        "challenge": "test", "manifest_sha256": "m", "baseline_sha256": "b", "verified": True,
        "submission_sha256": "s", "team": "blue", "successes": 3, "median_success_sim_s": 9.5,
    }), encoding="utf-8")
    output = tmp_path / "board" / "scoreboard.json"

    result = _invoke("classroom", "scoreboard", evaluation, "--output", output)

    assert result.exit_code == 0
    assert result.output == f"{output}\n"
    board, = json.loads(output.read_text())
    assert [entry["team"] for entry in board["entries"]] == ["blue"]


def test_classroom_scoreboard_rejects_missing_evaluation(tmp_path):
    result = _invoke("classroom", "scoreboard", tmp_path / "missing.json", "--output", tmp_path / "board.json")

    assert result.exit_code == 2
    assert "No such file or directory" in _plain(result)


# run / replay


def test_smoke_run_writes_bundle_without_simulator(tmp_path):
    output = tmp_path / "episode"

    result = _invoke("run", SCENARIO, "--output", output, "--smoke")

    assert result.exit_code == 0
    assert result.output == f"completed: {output}\n"
    assert json.loads((output / "result.json").read_text())["status"] == "completed"


def test_smoke_run_rejects_simulator_only_settings(tmp_path):
    params = tmp_path / "nav2.yaml"
    params.write_text("nav2: {}", encoding="utf-8")

    result = _invoke("run", SCENARIO, "--output", tmp_path / "episode", "--smoke", "--params-file", params)

    assert result.exit_code == 2
    assert "profiles require Gazebo" in _plain(result)
    assert not (tmp_path / "episode").exists()


def test_simulated_run_prints_score_when_baseline_was_applied(tmp_path, monkeypatch):
    calls = []

    def fake_gazebo(scenario, output, timeout, watchdog, *files):
        calls.append((output, timeout, watchdog, files))
        return EpisodeResult(status="completed", classification="behavior", score=6,
                             tiers_hit=["T2", "T3"], baseline_sha256="abc")

    monkeypatch.setattr(cli, "run_gazebo", fake_gazebo)
    output = tmp_path / "episode"

    result = _invoke("run", SCENARIO, "--output", output, "--timeout", "15", "--watchdog", "45")

    assert result.exit_code == 0
    assert result.output == f"completed: {output}\nscore=6 tiers=T2,T3\n"
    assert calls == [(output, 15.0, 45.0, (None, None, None, None))]


def test_failed_run_prints_reason_and_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_gazebo", lambda *args: EpisodeResult(
        status="mission_failed", classification="behavior", reason="goal 1 unreachable",
        baseline_sha256="abc",
    ))

    result = _invoke("run", SCENARIO, "--output", tmp_path / "episode")

    assert result.exit_code == 2
    assert "mission_failed" in result.output
    assert "score=0 tiers=-" in result.output
    assert "goal 1 unreachable" in result.output


@pytest.mark.parametrize("error, message", [
    (FileExistsError("exists"), "Choose a new output directory"),
    (ValueError("Baseline world, mission, or Nav2 parameters do not match"), "do not match"),
])
def test_run_turns_simulator_setup_errors_into_bad_parameter(tmp_path, monkeypatch, error, message):
    def failing(*args):
        raise error

    monkeypatch.setattr(cli, "run_gazebo", failing)

    result = _invoke("run", SCENARIO, "--output", tmp_path / "episode")

    assert result.exit_code == 2
    assert message in _plain(result)


def test_replay_requires_a_gazebo_bundle(tmp_path):
    bundle = tmp_path / "smoke"
    bundle.mkdir()

    result = _invoke("replay", bundle, "--output", tmp_path / "replay")

    assert result.exit_code == 2
    assert "Replay requires a Gazebo run bundle" in _plain(result)


def _gazebo_bundle(root: Path) -> Path:
    root.mkdir()
    (root / "scenario.json").write_text(json.dumps(Scenario().canonical_dict()), encoding="utf-8")
    (root / "run.json").write_text(json.dumps({"timeout": 12.0, "watchdog": 34.0}), encoding="utf-8")
    for name in ("oracle.yaml", "profile.yaml"):
        (root / name).write_text("{}\n", encoding="utf-8")
    return root


def test_replay_reuses_stored_settings_from_the_bundle(tmp_path, monkeypatch):
    bundle = _gazebo_bundle(tmp_path / "original")
    calls = []

    def fake_gazebo(scenario, output, timeout, watchdog, *files):
        calls.append((output, timeout, watchdog, files))
        return EpisodeResult(status="completed", classification="behavior")

    monkeypatch.setattr(cli, "run_gazebo", fake_gazebo)
    output = tmp_path / "replay"

    result = _invoke("replay", bundle, "--output", output)

    assert result.exit_code == 0
    assert calls == [(output, 12.0, 34.0, (None, None, bundle / "oracle.yaml", bundle / "profile.yaml"))]


def test_replay_profile_option_overrides_stored_profile(tmp_path, monkeypatch):
    bundle = _gazebo_bundle(tmp_path / "original")
    override = tmp_path / "other-profile.yaml"
    override.write_text("{}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "run_gazebo", lambda *args: calls.append(args) or EpisodeResult(
        status="completed", classification="behavior"))

    result = _invoke("replay", bundle, "--output", tmp_path / "replay", "--profile", override)

    assert result.exit_code == 0
    assert calls[0][-1] == override


# report / show


def _smoke_bundle(tmp_path: Path, with_telemetry: bool) -> Path:
    bundle = tmp_path / "episode"
    assert _invoke("run", SCENARIO, "--output", bundle, "--smoke").exit_code == 0
    if with_telemetry:
        rows = [{"ground_truth": {"sim_time": t, "x": t / 2, "y": 0}, "estimated": None} for t in range(3)]
        (bundle / "telemetry.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return bundle


def test_report_renders_trajectory_svg_into_bundle_by_default(tmp_path):
    bundle = _smoke_bundle(tmp_path, with_telemetry=True)

    result = _invoke("report", bundle)

    assert result.exit_code == 0
    assert result.output == f"{bundle / 'trajectory.svg'}\n"
    assert ET.parse(bundle / "trajectory.svg").getroot().tag.endswith("svg")


def test_report_rejects_bundle_without_telemetry(tmp_path):
    bundle = _smoke_bundle(tmp_path, with_telemetry=False)

    result = _invoke("report", bundle)

    assert result.exit_code == 2
    assert "no recorded telemetry" in _plain(result)


def test_report_on_campaign_forwards_filters_to_campaign_report(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / "plan.json").write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "render_campaign_report",
                        lambda root, target, **filters: calls.append((root, target, filters)))

    result = _invoke("report", campaign, "--strategy", "uniform", "--tier", "T2",
                     "--scenario-hash", "abc", "--min-score", "4")

    assert result.exit_code == 0
    assert result.output == f"{campaign / 'report.html'}\n"
    assert calls == [(campaign, campaign / "report.html",
                      {"strategy": "uniform", "tier": "T2", "scenario_hash": "abc", "minimum_score": 4})]


def test_show_writes_diagnostics_page(tmp_path):
    bundle = _smoke_bundle(tmp_path, with_telemetry=True)
    output = tmp_path / "diagnostics.html"

    result = _invoke("show", bundle, "--output", output)

    assert result.exit_code == 0
    assert result.output == f"{output}\n"
    assert "EnvFuzz run diagnostics" in output.read_text(encoding="utf-8")


def test_show_rejects_bundle_without_telemetry(tmp_path):
    bundle = _smoke_bundle(tmp_path, with_telemetry=False)

    result = _invoke("show", bundle)

    assert result.exit_code == 2
    assert "no recorded telemetry" in _plain(result)
    assert not (bundle / "diagnostics.html").exists()
