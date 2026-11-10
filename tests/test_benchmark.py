import csv
from pathlib import Path

from envfuzz.benchmark import route_exists, sample_scenarios
from envfuzz.campaign import prepare_campaign, run_campaign
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Pose, Scenario, ScenarioObject, Size


def test_reachability_detects_a_wall_to_wall_barrier():
    scenario = Scenario(
        start=Pose(x=-2, y=0),
        goals=[Pose(x=2, y=0)],
        objects=[ScenarioObject(id="wall", shape="box", pose=Pose(x=0, y=0),
                                size=Size(x=0.15, y=10))],
    )
    assert not route_exists(scenario)
    scenario.objects.clear()
    assert route_exists(scenario)


def test_stratified_sampler_visits_each_stratum_once_and_repeats():
    scenarios = sample_scenarios("corridor", "stratified", 42, 27)
    categories = {(case.challenge["width_bin"], case.challenge["clutter_count"],
                   case.challenge["goal_distance_bin"]) for case in scenarios}
    assert len(categories) == 27
    assert scenarios == sample_scenarios("corridor", "stratified", 42, 27)


def test_campaign_resume_keeps_completed_cases_and_retries_infrastructure(tmp_path, monkeypatch):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(
        "layouts: [corridor]\nmethods: [uniform]\nseeds: [3]\nepisodes_per_cell: 2\n",
        encoding="utf-8",
    )
    output = tmp_path / "runs"
    config, cases = prepare_campaign(manifest, output)
    calls = []

    def simulate(scenario, bundle: Path, timeout, watchdog):
        calls.append(bundle.name)
        bundle.mkdir(parents=True)
        if len(calls) == 2:
            result = EpisodeResult(status="watchdog", classification="infrastructure")
        else:
            result = EpisodeResult(status="completed", classification="behavior")
        (bundle / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
        return result

    monkeypatch.setattr("envfuzz.campaign.run_gazebo", simulate)
    assert not run_campaign(config, cases, output)
    assert run_campaign(*prepare_campaign(manifest, output), output)
    assert calls == ["attempt-000001", "attempt-000001", "attempt-000002"]
    with (output / "results.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["status"] for row in rows] == ["completed", "completed"]
