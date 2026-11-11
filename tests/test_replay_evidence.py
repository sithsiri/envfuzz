import json

from envfuzz.analysis import replay_corpus
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Scenario


def test_replay_reports_speed_and_separates_infrastructure(tmp_path, monkeypatch):
    original = tmp_path / "original"
    original.mkdir()
    (original / "scenario.json").write_text(Scenario().canonical_json(), encoding="utf-8")
    (original / "run.json").write_text('{"timeout":20,"watchdog":30}', encoding="utf-8")
    timeout = EpisodeResult(status="timeout", classification="behavior", real_time_factor=0.5)
    (original / "result.json").write_text(timeout.model_dump_json(), encoding="utf-8")

    def fake_worker(scenario, bundle, timeout_s, watchdog, limits):
        result = timeout if bundle.parent.name == "repeat-001" else EpisodeResult(
            status="watchdog", classification="infrastructure",
        )
        bundle.mkdir(parents=True)
        (bundle / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
        return result

    monkeypatch.setattr("envfuzz.analysis.run_worker", fake_worker)
    report = {"corpus": [{"representative": "original", "signature": ["corridor"]}]}
    output = tmp_path / "analysis.json"
    replay_corpus(tmp_path, output, report, repeats=2, workers=1, image="unused")
    case, = json.loads(output.read_text(encoding="utf-8"))["corpus"]
    assert case["valid_replays"] == 1
    assert case["matching_replays"] == 1
    assert [run["real_time_factor"] for run in case["replays"]] == [0.5, None]
    assert [run["matched"] for run in case["replays"]] == [True, False]
