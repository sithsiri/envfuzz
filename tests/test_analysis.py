"""Campaign budget comparison, regression signatures, and corpus replay with fake workers."""

import json
from pathlib import Path

import pytest

from envfuzz import analysis
from envfuzz.analysis import analyze_campaign, regression_signature, replay_corpus
from envfuzz.results import EpisodeEvent, EpisodeResult
from envfuzz.scenario import Scenario

CHALLENGE = {"layout": "corridor", "width_bin": "narrow", "clutter_count": 1, "goal_distance_bin": "near"}


def _write_result(bundle: Path, result: EpisodeResult, terminal: tuple[float, float] | None = None) -> Path:
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    if terminal is not None:
        rows = [{"ground_truth": {"sim_time": 0, "x": 0, "y": 0}, "estimated": None},
                {"ground_truth": {"sim_time": 1, "x": terminal[0], "y": terminal[1]}, "estimated": None}]
        (bundle / "telemetry.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return bundle


def _failed() -> EpisodeResult:
    return EpisodeResult(status="mission_failed", classification="behavior")


def _map_error_completion() -> EpisodeResult:
    return EpisodeResult(status="completed", classification="behavior",
                         events=[EpisodeEvent(tier="T3", kind="map_error", sim_start=2)])


@pytest.mark.parametrize("result", [
    EpisodeResult(status="watchdog", classification="infrastructure"),
    EpisodeResult(status="interrupted", classification="interrupted"),
    EpisodeResult(status="completed", classification="behavior"),
])
def test_signature_ignores_non_behavior_and_clean_completions(tmp_path, result):
    bundle = _write_result(tmp_path / "bundle", result, terminal=(1, 1))

    assert regression_signature(bundle, "corridor") is None


def test_signature_rounds_terminal_position_to_one_meter_grid(tmp_path):
    bundle = _write_result(tmp_path / "bundle", _failed(), terminal=(1.49, -2.5))

    assert regression_signature(bundle, "corridor") == ("corridor", "mission_failed", False, (1, -2))


def test_signature_marks_completed_runs_with_map_error_and_missing_telemetry(tmp_path):
    bundle = _write_result(tmp_path / "bundle", _map_error_completion())

    assert regression_signature(bundle, "office") == ("office", "completed", True, None)


def _campaign(root: Path, methods: list[str], case_methods: list[str]) -> Path:
    root.mkdir(parents=True)
    plan = {
        "manifest": {"layouts": ["corridor"], "methods": methods},
        "cases": [{"layout": "corridor", "method": method,
                   "scenario": Scenario(seed=index, challenge=CHALLENGE).canonical_dict()}
                  for index, method in enumerate(case_methods, 1)],
    }
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return root


def _attempt(root: Path, case: int, attempt: int) -> Path:
    return root / f"case-{case:06d}" / f"attempt-{attempt:06d}"


def test_campaign_analysis_compares_strategies_at_shared_budget(tmp_path):
    root = _campaign(tmp_path / "campaign", ["uniform", "stratified"],
                     ["uniform", "uniform", "stratified", "stratified"])
    _write_result(_attempt(root, 1, 1), EpisodeResult(status="watchdog", classification="infrastructure"))
    _write_result(_attempt(root, 1, 2) / "bundle", _failed(), terminal=(2.2, 0.4))
    _write_result(_attempt(root, 2, 1), EpisodeResult(status="completed", classification="behavior"))
    _write_result(_attempt(root, 2, 2), EpisodeResult(status="completed", classification="behavior"))
    _write_result(_attempt(root, 3, 1), _failed(), terminal=(1.8, -0.3))
    _write_result(_attempt(root, 4, 1), EpisodeResult(status="interrupted", classification="interrupted"))
    output = tmp_path / "reports" / "analysis.json"

    report = analyze_campaign(root, output)

    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(json.dumps(report))
    assert report["shared_budget"] == 1
    assert report["shared_budget_by_layout"] == {"corridor": 1}
    uniform, stratified = report["strategies"]["uniform"], report["strategies"]["stratified"]
    assert uniform == {
        "completed_behavior_episodes": 2, "infrastructure_attempts": 1, "interrupted_attempts": 0,
        "outcomes": {"mission_failed": 1}, "covered_categories": 1, "distinct_cases_by_episode": [1],
    }
    assert stratified["completed_behavior_episodes"] == 1
    assert stratified["interrupted_attempts"] == 1
    assert stratified["distinct_cases_by_episode"] == [1]
    corpus, = report["corpus"]
    assert corpus["signature"] == ("corridor", "mission_failed", False, (2, 0))
    assert corpus["representative"] == "case-000001/attempt-000002/bundle"
    assert [run["strategy"] for run in corpus["runs"]] == ["uniform", "stratified"]
    assert json.loads(output.read_text())["corpus"][0]["signature"] == ["corridor", "mission_failed", False, [2, 0]]


def test_campaign_analysis_without_matching_behavior_has_zero_budget(tmp_path):
    root = _campaign(tmp_path / "campaign", ["uniform", "stratified"], ["uniform"])
    _write_result(_attempt(root, 1, 1), EpisodeResult(status="completed", classification="behavior"))

    report = analyze_campaign(root, tmp_path / "analysis.json")

    assert report["shared_budget"] == 0
    assert report["strategies"]["uniform"]["distinct_cases_by_episode"] == []
    assert report["corpus"] == []


def _replay_report(root: Path) -> dict:
    original = _write_result(root / "case-000001" / "attempt-000001", _failed(), terminal=(3.0, 0.0))
    (original / "scenario.json").write_text(json.dumps(Scenario().canonical_dict()), encoding="utf-8")
    (original / "run.json").write_text(json.dumps({"timeout": 30, "watchdog": 90}), encoding="utf-8")
    (original / "baseline.json").write_text("{}", encoding="utf-8")
    (original / "nav2.yaml").write_text("nav2: {}", encoding="utf-8")
    return {"shared_budget": 1, "corpus": [{
        "signature": ["corridor", "mission_failed", False, [3, 0]],
        "representative": "case-000001/attempt-000001", "runs": [],
    }]}


def test_corpus_replay_measures_reproduction_rate_with_fake_workers(tmp_path, monkeypatch):
    root = tmp_path / "campaign"
    report = _replay_report(root)
    outcomes = iter([
        (_failed(), (3.1, 0.2)),
        (_failed(), (-1.0, 0.0)),
        (EpisodeResult(status="watchdog", classification="infrastructure"), None),
    ])
    calls = []

    def fake_worker(scenario, bundle, timeout, watchdog, limits, **files):
        calls.append((bundle, timeout, watchdog, limits.image, files))
        result, terminal = next(outcomes)
        _write_result(bundle, result, terminal)
        return result

    monkeypatch.setattr(analysis, "run_worker", fake_worker)
    output = tmp_path / "analysis.json"

    replay_corpus(root, output, report, repeats=3, workers=1, image="envfuzz:test")

    case, = report["corpus"]
    assert (case["valid_replays"], case["matching_replays"], case["reproduction_rate"]) == (2, 1, 0.5)
    assert [row["matched"] for row in case["replays"]] == [True, False, False]
    assert case["replays"][0]["bundle"] == "corpus-replays/case-0001/repeat-001/bundle"
    assert {call[1:4] for call in calls} == {(30, 90, "envfuzz:test")}
    assert set(calls[0][4]) == {"baseline", "params"}
    assert json.loads(output.read_text())["corpus"][0]["reproduction_rate"] == 0.5


def test_corpus_replay_reuses_finished_repeats_and_retries_partial_ones(tmp_path, monkeypatch):
    root = tmp_path / "campaign"
    report = _replay_report(root)
    replays = root / "corpus-replays" / "case-0001"
    _write_result(replays / "repeat-001" / "bundle", _failed(), terminal=(3.0, 0.0))
    (replays / "repeat-002").mkdir(parents=True)
    calls = []

    def fake_worker(scenario, bundle, timeout, watchdog, limits, **files):
        calls.append(bundle.relative_to(root).as_posix())
        result = EpisodeResult(status="watchdog", classification="infrastructure")
        _write_result(bundle, result)
        return result

    monkeypatch.setattr(analysis, "run_worker", fake_worker)

    replay_corpus(root, tmp_path / "analysis.json", report, repeats=2, workers=2, image="envfuzz:test")

    assert calls == ["corpus-replays/case-0001/repeat-002-retry-1/bundle"]
    case, = report["corpus"]
    assert (case["valid_replays"], case["matching_replays"], case["reproduction_rate"]) == (1, 1, 1.0)


def test_corpus_replay_reports_no_rate_when_every_repeat_is_infrastructure(tmp_path, monkeypatch):
    root = tmp_path / "campaign"
    report = _replay_report(root)

    def fake_worker(scenario, bundle, timeout, watchdog, limits, **files):
        result = EpisodeResult(status="simulator_failed", classification="infrastructure")
        _write_result(bundle, result)
        return result

    monkeypatch.setattr(analysis, "run_worker", fake_worker)

    replay_corpus(root, tmp_path / "analysis.json", report, repeats=1, workers=1, image="envfuzz:test")

    assert report["corpus"][0]["reproduction_rate"] is None
    assert report["corpus"][0]["valid_replays"] == 0
