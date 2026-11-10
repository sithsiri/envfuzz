import json
import math
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from envfuzz.scenario import Pose, Scenario, ScenarioObject, Size, compile_sdf, validate_scenario


def test_hash_and_compilation_are_stable():
    scenario = Scenario(objects=[ScenarioObject(id="box", shape="box", pose=Pose(x=2, y=2),
                                                size=Size(x=1, y=1, z=1))])
    assert scenario.content_hash() == Scenario.model_validate_json(scenario.model_dump_json()).content_hash()
    assert compile_sdf(scenario) == compile_sdf(scenario)


def test_invalid_overlap_is_specific():
    scenario = Scenario(objects=[
        ScenarioObject(id="a", shape="cylinder", pose=Pose(x=2, y=2), radius=1),
        ScenarioObject(id="b", shape="cylinder", pose=Pose(x=2.5, y=2), radius=1),
    ])
    assert any("a/b" in violation for violation in validate_scenario(scenario))


def test_canonical_json_is_sorted():
    scenario = Scenario()
    assert json.loads(scenario.canonical_json()) == scenario.canonical_dict()


def test_world_includes_solid_room_and_visible_obstacles():
    scenario = Scenario(
        world={"min_x": -3, "max_x": 4, "min_y": -2, "max_y": 5},
        objects=[
            ScenarioObject(id="floor", shape="box", pose=Pose(x=3, y=3, yaw=1),
                           size=Size(x=0.5, y=1, z=0.8)),
            ScenarioObject(id="stool", shape="cylinder", pose=Pose(x=0, y=3),
                           radius=0.2, height=0.6),
        ],
    )
    world = ET.fromstring(compile_sdf(scenario)).find("world")
    assert world is not None
    assert {plugin.get("filename") for plugin in world.findall("plugin")} == {
        "gz-sim-physics-system", "gz-sim-user-commands-system",
        "gz-sim-sensors-system", "gz-sim-imu-system",
    }
    models = {model.get("name"): model for model in world.findall("model")}
    assert models.keys() == {
        "floor", "wall_west", "wall_east", "wall_south", "wall_north",
        "obstacle_floor", "obstacle_stool",
    }
    assert models["floor"].findtext("pose") == "0.5 1.5 -0.05 0 0 0"
    assert models["obstacle_floor"].findtext("pose") == "3.0 3.0 0.4 0 0 1.0"
    assert models["obstacle_stool"].findtext("pose") == "0.0 3.0 0.3 0 0 0.0"
    for model in models.values():
        collision = model.find("link/collision/geometry")
        visual = model.find("link/visual/geometry")
        assert collision is not None and visual is not None
        assert ET.tostring(collision).strip() == ET.tostring(visual).strip()


def test_rotated_box_crossing_a_wall_is_rejected():
    obstacle = ScenarioObject(id="bench", shape="box", pose=Pose(x=3, y=4.5, yaw=math.pi / 2),
                              size=Size(x=2, y=0.2))
    assert validate_scenario(Scenario(objects=[obstacle])) == ["bench: object exceeds world bounds"]


@pytest.mark.parametrize("shape, field", [("box", "size"), ("cylinder", "radius")])
def test_missing_shape_dimensions_are_rejected(shape, field):
    with pytest.raises(ValidationError, match=f"{field} is required"):
        ScenarioObject(id="obstacle", shape=shape, pose=Pose(x=3, y=3))

