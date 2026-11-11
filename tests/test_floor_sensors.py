import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from envfuzz.gazebo import run_gazebo
from envfuzz.scenario import Pose, compile_sdf, load_scenario, validate_scenario
from envfuzz.scoring import read_trace

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_floor_regions_affect_geometry_and_validate_clearance():
    scenario = load_scenario(EXAMPLES / "cliff-drop.yaml")
    world = ET.fromstring(compile_sdf(scenario)).find("world")
    drop, = [model for model in world.findall("model") if model.findtext("pose").split()[2] == "-0.55"]
    assert drop.findtext("link/collision/geometry/box/size").split()[2] == "0.1"
    scenario.goals = [Pose(x=1.5, y=0)]
    assert validate_scenario(scenario) == ["goal 1: violates floor drop clearance"]

    patch = load_scenario(EXAMPLES / "friction-patch.yaml")
    world = ET.fromstring(compile_sdf(patch)).find("world")
    mu, = world.findall(".//collision/surface/friction/ode/mu")
    assert float(mu.text) == 0
    assert "floor_patches" not in load_scenario(EXAMPLES / "scenario.yaml").canonical_dict()


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ENVFUZZ_GAZEBO_TEST") != "1", reason="requires ROS 2 and Gazebo")
def test_cliff_sensor_observes_recessed_floor(tmp_path):
    scenario = load_scenario(EXAMPLES / "cliff-drop.yaml")
    bundle = tmp_path / "cliff"
    result = run_gazebo(scenario, bundle, timeout=15, watchdog=90,
                        profile_file=EXAMPLES / "cliff-profile.yaml")
    assert result.classification == "behavior", result.reason
    rows = [json.loads(line) for line in (bundle / "cliff.jsonl").read_text().splitlines()]
    assert any(row["detected"] and row["sim_time"] > 0
               and row["range_m"] is not None and row["range_m"] >= 0.4 for row in rows)


def _displacement_error(bundle):
    motion = [json.loads(line) for line in (bundle / "motion.jsonl").read_text().splitlines()]
    assert any(row["kind"] == "imu" for row in motion)
    trace, _ = read_trace(bundle / "telemetry.jsonl")
    odometry = [row for row in motion
                if row["kind"] == "wheel_odometry" and trace[0].sim_time <= row["sim_time"] <= trace[-1].sim_time]
    assert len(odometry) >= 2
    first, last = odometry[0], odometry[-1]
    start = min(trace, key=lambda row: abs(row.sim_time - first["sim_time"]))
    end = min(trace, key=lambda row: abs(row.sim_time - last["sim_time"]))
    return abs((last["x"] - first["x"]) - (end.x - start.x))


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ENVFUZZ_GAZEBO_TEST") != "1", reason="requires ROS 2 and Gazebo")
def test_low_friction_increases_wheel_odometry_error(tmp_path):
    patch = load_scenario(EXAMPLES / "friction-patch.yaml")
    control = patch.model_copy(update={"floor_patches": None})
    for name, scenario in (("uniform", control), ("patch", patch)):
        result = run_gazebo(scenario, tmp_path / name, timeout=12, watchdog=90,
                            profile_file=EXAMPLES / "motion-profile.yaml")
        assert result.classification == "behavior", result.reason
    uniform_error = _displacement_error(tmp_path / "uniform")
    patch_error = _displacement_error(tmp_path / "patch")
    assert uniform_error < 0.25
    assert patch_error > uniform_error + 0.3
