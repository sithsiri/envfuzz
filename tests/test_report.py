"""Trajectory SVG and diagnostics HTML rendering from synthetic run bundles."""

import json
from xml.etree import ElementTree as ET

import pytest

from envfuzz.report import render_diagnostics, render_report
from envfuzz.results import EpisodeEvent, EpisodeResult
from envfuzz.scenario import Pose, Scenario, ScenarioObject, Size, Zone

SVG = "{http://www.w3.org/2000/svg}"


def _scenario() -> Scenario:
    return Scenario(
        objects=[
            ScenarioObject(id="crate", shape="box", pose=Pose(x=2, y=2, yaw=0.5), size=Size(x=1, y=0.5)),
            ScenarioObject(id="post", shape="cylinder", pose=Pose(x=-2, y=-2), radius=0.25),
        ],
        goals=[Pose(x=2, y=0), Pose(x=3, y=-3)],
        restricted_zones=[Zone(id="dock", polygon=[(-4, 3), (-3, 3), (-3, 4)])],
    )


def _write_bundle(root, scenario, result, telemetry_rows):
    root.mkdir(parents=True)
    (root / "scenario.json").write_text(json.dumps(scenario.canonical_dict()), encoding="utf-8")
    (root / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    if telemetry_rows is not None:
        (root / "telemetry.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in telemetry_rows), encoding="utf-8",
        )
    return root


def _row(time, x, y, estimated=True):
    truth = {"sim_time": time, "x": x, "y": y}
    return {"ground_truth": truth, "estimated": {**truth, "x": x + 0.1} if estimated else None}


def _texts(root) -> list[str]:
    return [element.text for element in root.iter(f"{SVG}text")]


def test_trajectory_plot_draws_world_traces_markers_and_events(tmp_path):
    result = EpisodeResult(
        status="mission_failed", classification="behavior", sim_duration=12.345,
        events=[EpisodeEvent(tier="T2", kind="stuck", sim_start=1.9)],
    )
    bundle = _write_bundle(tmp_path / "run", _scenario(), result,
                           [_row(0, 0, 0), _row(1, 0.5, 0, estimated=False), _row(2, 1.0, 0.2)])
    output = tmp_path / "nested" / "trajectory.svg"

    render_report(bundle, output)

    root = ET.parse(output).getroot()
    assert root.tag == f"{SVG}svg"
    assert "Mission failed · 12.35 s simulation time" in _texts(root)
    assert {"Start", "Goal 1", "Goal 2", "Gazebo ground truth", "Aligned SLAM estimate"} <= set(_texts(root))
    assert len(root.findall(f"{SVG}polygon")) == 1
    crate = [rect for rect in root.findall(f"{SVG}rect") if rect.get("transform")]
    assert len(crate) == 1 and crate[0].get("transform").startswith("rotate(-28.64")
    truth_line, estimate_line = root.findall(f"{SVG}polyline")
    assert len(truth_line.get("points").split()) == 3
    assert len(estimate_line.get("points").split()) == 2
    assert estimate_line.get("stroke-dasharray") == "5 3"
    event_markers = [circle for circle in root.findall(f"{SVG}circle") if circle.find(f"{SVG}title") is not None]
    assert [marker.find(f"{SVG}title").text for marker in event_markers] == ["stuck"]
    # The event at 1.9 s is drawn at the nearest ground-truth sample (t=2, x=1.0, y=0.2).
    scale = min(640 / 10, 520 / 10)
    assert float(event_markers[0].get("cx")) == pytest.approx(60 + 6.0 * scale)
    assert float(event_markers[0].get("cy")) == pytest.approx(90 + 4.8 * scale)


def test_trajectory_plot_omits_estimate_legend_without_slam_samples(tmp_path):
    result = EpisodeResult(status="completed", classification="behavior")
    bundle = _write_bundle(tmp_path / "run", Scenario(), result,
                           [_row(0, 0, 0, estimated=False), _row(1, 1, 0, estimated=False)])
    output = tmp_path / "trajectory.svg"

    render_report(bundle, output)

    root = ET.parse(output).getroot()
    assert "Aligned SLAM estimate" not in _texts(root)
    assert len(root.findall(f"{SVG}polyline")) == 1


def test_trajectory_plot_requires_recorded_telemetry(tmp_path):
    result = EpisodeResult(status="launch_failed", classification="infrastructure")
    bundle = _write_bundle(tmp_path / "run", Scenario(), result, None)

    with pytest.raises(ValueError, match="no recorded telemetry"):
        render_report(bundle, tmp_path / "trajectory.svg")

    assert not (tmp_path / "trajectory.svg").exists()


def test_trajectory_plot_requires_ground_truth_samples(tmp_path):
    result = EpisodeResult(status="completed", classification="behavior")
    bundle = _write_bundle(tmp_path / "run", Scenario(), result, [])

    with pytest.raises(ValueError, match="no recorded ground-truth samples"):
        render_report(bundle, tmp_path / "trajectory.svg")


def _grid(data, width=3, height=2):
    return json.dumps({"width": width, "height": height, "resolution": 1.0,
                       "origin": {"x": 0, "y": 0}, "data": data})


def test_diagnostics_without_maps_embed_only_the_trajectory(tmp_path):
    result = EpisodeResult(status="completed", classification="behavior")
    bundle = _write_bundle(tmp_path / "run", Scenario(), result, [_row(0, 0, 0)])
    output = tmp_path / "out" / "diagnostics.html"

    render_diagnostics(bundle, output)

    html = output.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "<h1>EnvFuzz run diagnostics</h1>" in html
    assert "Navigation trajectory" in html
    assert "<?xml" not in html
    assert "Aligned occupancy maps" not in html


def test_diagnostics_color_each_occupancy_agreement_case(tmp_path):
    result = EpisodeResult(status="completed", classification="behavior")
    bundle = _write_bundle(tmp_path / "run", Scenario(), result, [_row(0, 0, 0)])
    # Cells in row-major order from the bottom row: occupied match, free match, missing,
    # unexpected, unknown estimate, and a second free match.
    (bundle / "reference-map.json").write_text(_grid([100, 0, 100, 0, 100, 0]), encoding="utf-8")
    (bundle / "slam-map.json").write_text(_grid([100, 0, 0, 100, -1, 0]), encoding="utf-8")
    output = tmp_path / "diagnostics.html"

    render_diagnostics(bundle, output)

    html = output.read_text(encoding="utf-8")
    assert "Aligned occupancy maps" in html
    panel = ET.fromstring(html[html.index('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 2"'):
                               html.rindex("</svg>") + len("</svg>")])
    fills = {(int(rect.get("x")), int(rect.get("y"))): rect.get("fill") for rect in panel.iter(f"{SVG}rect")}
    assert fills == {
        (0, 1): "#344054", (1, 1): "#ffffff", (2, 1): "#175cd3",
        (0, 0): "#b54708", (1, 0): "#d0d5dd", (2, 0): "#ffffff",
    }


def test_diagnostics_ignore_a_single_unpaired_map(tmp_path):
    result = EpisodeResult(status="completed", classification="behavior")
    bundle = _write_bundle(tmp_path / "run", Scenario(), result, [_row(0, 0, 0)])
    (bundle / "reference-map.json").write_text(_grid([0] * 6), encoding="utf-8")
    output = tmp_path / "diagnostics.html"

    render_diagnostics(bundle, output)

    assert "Aligned occupancy maps" not in output.read_text(encoding="utf-8")
