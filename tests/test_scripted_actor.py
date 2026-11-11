import json
import math
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from envfuzz.cli import app
from envfuzz.gazebo import run_gazebo
from envfuzz.scenario import (
    Pose, ScenarioObject, TimedMove, compile_sdf, load_scenario, timeline_moves, validate_scenario,
)
from envfuzz.scoring import read_trace

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "scripted-actor.yaml"


def test_actor_schedule_is_deterministic_and_preserves_existing_events():
    scenario = load_scenario(EXAMPLE)
    assert not validate_scenario(scenario)
    moves = timeline_moves(scenario)
    assert len(moves) == 40
    assert moves[29].at == 3
    assert moves[34].pose == Pose(x=2, y=2)
    assert moves[-1].at == 4
    world = compile_sdf(scenario)
    assert world == compile_sdf(load_scenario(EXAMPLE))
    plugin = ET.fromstring(world).find("./world/plugin[@name='envfuzz::Timeline']")
    assert [int(move.findtext("nanoseconds")) for move in plugin] == [
        round(move.at * 1e9) for move in moves
    ]
    old = load_scenario(EXAMPLE.with_name("timed-move.yaml"))
    assert timeline_moves(old) == old.events
    assert "actors" not in old.canonical_dict()


def test_actor_paths_obey_clearance_and_timeline_budget():
    scenario = load_scenario(EXAMPLE)
    scenario.actors[0].keyframes[-1].pose = Pose(x=7, y=2)
    assert any("world bounds" in issue for issue in validate_scenario(scenario))

    scenario = load_scenario(EXAMPLE)
    scenario.objects.append(ScenarioObject(
        id="post", shape="cylinder", pose=Pose(x=2, y=2), radius=0.05,
    ))
    assert any("objects overlap" in issue for issue in validate_scenario(scenario))

    scenario = load_scenario(EXAMPLE)
    scenario.actors[0].keyframes[-1].at = 1
    assert validate_scenario(scenario) == ["actor walker: keyframe times must increase"]

    scenario = load_scenario(EXAMPLE)
    scenario.actors[0].keyframes[-1].at = 101
    assert validate_scenario(scenario) == ["actor paths and events exceed the timeline move limit"]

    scenario = load_scenario(EXAMPLE)
    scenario.events = [TimedMove(at=2, object_id="walker", pose=Pose(x=2, y=2))]
    assert validate_scenario(scenario) == ["actor walker: conflicting timeline tracks"]


def _scan_hits(scans, trace, x, y):
    for scan in scans:
        robot = min(trace, key=lambda pose: abs(pose.sim_time - scan["sim_time"]))
        if abs(robot.sim_time - scan["sim_time"]) > 0.15:
            continue
        for index, distance in enumerate(scan["ranges"]):
            if distance is None:
                continue
            angle = robot.yaw + scan["angle_min"] + index * scan["angle_increment"]
            hit_x = robot.x + distance * math.cos(angle)
            hit_y = robot.y + distance * math.sin(angle)
            if math.hypot(hit_x - x, hit_y - y) < 0.35:
                return True
    return False


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ENVFUZZ_GAZEBO_TEST") != "1", reason="requires ROS 2 and Gazebo")
def test_scripted_actor_replays_and_appears_in_lidar(tmp_path):
    scenario = load_scenario(EXAMPLE)
    applied_times = []
    for trial in ("first", "replay"):
        bundle = tmp_path / trial
        if trial == "first":
            result = run_gazebo(scenario, bundle, timeout=30, watchdog=90)
            assert result.status == "completed", result.reason
        else:
            replay = CliRunner().invoke(app, [
                "replay", str(tmp_path / "first"), "--output", str(bundle),
            ])
            assert replay.exit_code == 0, replay.output or replay.exception
        records = json.loads((bundle / "events-applied.json").read_text())
        assert len(records) == 40
        assert records[-1]["pose"] == {"x": 2, "y": 2.5, "yaw": 0}
        requested = [row["requested_sim_time"] for row in records]
        applied = [row["applied_sim_time"] for row in records]
        assert applied == pytest.approx(requested, abs=0.001)
        applied_times.append(applied)
        scans = [json.loads(line) for line in (bundle / "scans.jsonl").read_text().splitlines()]
        trace, _ = read_trace(bundle / "telemetry.jsonl")
        assert _scan_hits([scan for scan in scans if scan["sim_time"] < 3], trace, 2, 1.5)
        assert _scan_hits([scan for scan in scans if scan["sim_time"] > 4], trace, 2, 2.5)
    assert applied_times[0] == pytest.approx(applied_times[1], abs=0.001)
