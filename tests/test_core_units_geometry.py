"""Unit tests for scenario validity rules, SDF compilation, occupancy maps and material rays."""

from __future__ import annotations

import json
import math
from xml.etree import ElementTree as ET

import pytest

from envfuzz.maps import Grid, distance_to_obstacle, reference_grid, save_map_comparison, unreachable_waypoint
from envfuzz.materials import reference_rays
from envfuzz.scenario import (
    FloorRegion, Pose, Scenario, ScenarioObject, Size, compile_sdf, load_scenario, timeline_moves,
    validate_scenario,
)


def _box(object_id: str, x: float, y: float, size: float = 0.4, height: float = 0.6, **extra):
    return ScenarioObject(id=object_id, shape="box", pose=Pose(x=x, y=y),
                          size=Size(x=size, y=size, z=height), **extra)


def _cylinder(object_id: str, x: float, y: float, radius: float = 0.2, height: float = 1.0, **extra):
    return ScenarioObject(id=object_id, shape="cylinder", pose=Pose(x=x, y=y), radius=radius,
                          height=height, **extra)


# --- schema -----------------------------------------------------------------------------------


@pytest.mark.parametrize("data, message", [
    ({"version": "3"}, "unsupported scenario version"),
    ({"objects": [{"id": "a", "shape": "box", "pose": {"x": 3, "y": 3}, "size": {"x": 1, "y": 1}}] * 2},
     "must be unique"),
    ({"events": []}, "events requires scenario version 2"),
    ({"objects": [{"id": "a", "shape": "box", "pose": {"x": 3, "y": 3}}]}, "size is required"),
    ({"objects": [{"id": "a", "shape": "cylinder", "pose": {"x": 3, "y": 3}}]}, "radius is required"),
    ({"objects": [{"id": "../a", "shape": "cylinder", "pose": {"x": 3, "y": 3}, "radius": 1}]},
     "pattern"),
    ({"start": {"x": float("nan"), "y": 0}}, "finite"),
])
def test_schema_rejects_invalid_documents(data, message):
    # Act / Assert
    with pytest.raises(ValueError, match=message):
        Scenario.model_validate(data)


def test_floor_regions_require_increasing_bounds():
    # Act / Assert
    with pytest.raises(ValueError, match="must be increasing"):
        FloorRegion(min_x=1.0, max_x=1.0, min_y=0.0, max_y=1.0)


def test_scenario_file_root_must_be_a_mapping(tmp_path):
    # Arrange
    path = tmp_path / "scenario.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match="root must be a mapping"):
        load_scenario(path)


# --- validity rules ---------------------------------------------------------------------------


@pytest.mark.parametrize("world", [
    {"min_x": 1.0, "max_x": -1.0, "min_y": -5.0, "max_y": 5.0},
    {"min_x": -5.0, "max_x": 5.0, "min_y": -5.0},
])
def test_invalid_world_bounds_short_circuit_validation(world):
    # Arrange
    scenario = Scenario(world=world)

    # Act
    violations = validate_scenario(scenario)

    # Assert
    assert len(violations) == 1
    assert "must be finite and increasing" in violations[0]


def test_floor_regions_must_fit_world_and_not_overlap():
    # Arrange
    scenario = Scenario.model_validate({
        "version": "2",
        "floor_patches": [
            {"min_x": 3.0, "max_x": 6.0, "min_y": 3.0, "max_y": 4.0, "friction": 0.2},
            {"min_x": 3.5, "max_x": 4.5, "min_y": 3.5, "max_y": 4.5, "friction": 0.5},
        ],
    })

    # Act
    violations = validate_scenario(scenario)

    # Assert
    assert violations == ["floor region 1: exceeds world bounds", "floor region 2: overlaps an earlier region"]


def test_objects_and_mission_poses_keep_clear_of_drops_and_walls():
    # Arrange
    scenario = Scenario.model_validate({
        "version": "2", "start": {"x": -4.9, "y": 0.0}, "goals": [{"x": 2.0, "y": 0.0}],
        "floor_drops": [{"min_x": 2.2, "max_x": 3.0, "min_y": -1.0, "max_y": 1.0, "depth_m": 0.3}],
        "objects": [{"id": "rim", "shape": "box", "pose": {"x": 3.1, "y": 2.0}, "size": {"x": 0.4, "y": 0.4}},
                    {"id": "edge", "shape": "box", "pose": {"x": 4.9, "y": -3.0}, "size": {"x": 0.4, "y": 0.4}},
                    {"id": "dip", "shape": "box", "pose": {"x": 3.1, "y": 0.5}, "size": {"x": 0.4, "y": 0.4}}],
    })

    # Act
    violations = validate_scenario(scenario)

    # Assert
    assert "start: violates wall clearance" in violations
    assert "goal 1: violates floor drop clearance" in violations
    assert "rim: overlaps a floor drop" not in violations  # 1 m away from the drop
    assert "dip: overlaps a floor drop" in violations  # 0.1 m from the drop edge, radius 0.28 m
    assert "edge: object exceeds world bounds" in violations


def test_objects_inside_restricted_zones_are_rejected_only_when_forbidden():
    # Arrange
    zone = {"id": "lab", "polygon": [[-3, 1], [-1, 1], [-1, 3], [-3, 3]]}
    data = {"objects": [{"id": "crate", "shape": "box", "pose": {"x": -2, "y": 2},
                         "size": {"x": 0.4, "y": 0.4}}], "restricted_zones": [zone]}

    # Act
    allowed = validate_scenario(Scenario.model_validate(data))
    forbidden = validate_scenario(Scenario.model_validate({**data, "challenge": {"forbid_restricted_objects": True}}))

    # Assert
    assert allowed == []
    assert forbidden == ["crate: object is inside restricted zone lab"]


def test_reachability_reports_blocked_start_and_unreachable_goal():
    # Arrange
    narrow = {"min_x": -1.0, "max_x": 4.0, "min_y": -0.6, "max_y": 0.6}
    blocked_goal = Scenario(world=narrow, objects=[_box("wall", 1.0, 0.0, size=0.1)],
                            challenge={"require_reachable": True})
    blocked_goal = blocked_goal.model_copy(update={"objects": [ScenarioObject(
        id="wall", shape="box", pose=Pose(x=1.0, y=0.0), size=Size(x=0.1, y=1.1))]})
    hugging_wall = Scenario(start=Pose(x=-4.65, y=0.0), challenge={"require_reachable": True})

    # Act / Assert
    assert validate_scenario(blocked_goal) == ["goal 1: no route with robot footprint clearance"]
    assert validate_scenario(hugging_wall) == ["start: no route with robot footprint clearance"]
    assert validate_scenario(Scenario(challenge={"require_reachable": True})) == []


def test_timed_events_must_be_ordered_and_reference_objects():
    # Arrange
    chair = {"id": "chair", "shape": "box", "pose": {"x": 2, "y": 2}, "size": {"x": 0.4, "y": 0.4}}
    unordered = Scenario.model_validate({"version": "2", "objects": [chair], "events": [
        {"at": 3, "object_id": "chair", "pose": {"x": 2, "y": 1.5}},
        {"at": 1, "object_id": "chair", "pose": {"x": 2, "y": 2.5}}]})
    dangling = Scenario.model_validate({"version": "2", "objects": [chair], "events": [
        {"at": 1, "object_id": "ghost", "pose": {"x": 2, "y": 1.5}}]})
    collision = Scenario.model_validate({"version": "2", "objects": [chair], "events": [
        {"at": 1, "object_id": "chair", "pose": {"x": 2, "y": 0.1}}]})

    # Act / Assert
    assert validate_scenario(unordered) == ["event order is invalid"]
    assert validate_scenario(dangling) == ["event 1: object reference is invalid"]
    assert validate_scenario(collision) == ["event 1: chair: violates goal clearance"]


def _actor_scenario(keyframes: list[dict], object_id: str = "walker") -> Scenario:
    return Scenario.model_validate({
        "version": "2", "goals": [{"x": 4, "y": 0}],
        "objects": [{"id": "walker", "shape": "cylinder", "pose": {"x": 2, "y": 1.5}, "radius": 0.2}],
        "actors": [{"object_id": object_id, "keyframes": keyframes}],
    })


@pytest.mark.parametrize("keyframes, object_id, message", [
    ([{"at": 1, "pose": {"x": 2, "y": 2}}], "ghost", "actor ghost: unknown object"),
    ([{"at": 3600, "pose": {"x": 200, "y": 1.5}}], "walker", "exceed the timeline move limit"),
    ([{"at": 1e-9, "pose": {"x": 2, "y": 2.5}}], "walker", "keyframes are too close in time"),
])
def test_actor_paths_are_bounded(keyframes, object_id, message):
    # Arrange
    scenario = _actor_scenario(keyframes, object_id)

    # Act / Assert
    with pytest.raises(ValueError, match=message):
        timeline_moves(scenario)
    assert any(message in violation for violation in validate_scenario(scenario))


def test_actor_moves_are_interpolated_in_order():
    # Arrange
    scenario = _actor_scenario([{"at": 0.3, "pose": {"x": 2, "y": 1.8, "yaw": 0.3}}])

    # Act
    moves = timeline_moves(scenario)

    # Assert
    times = [move.at for move in moves]
    ys = [1.5, *(move.pose.y for move in moves)]
    assert len(moves) >= 3
    assert times == sorted(set(times)) and times[-1] == 0.3
    assert all(0 < later - earlier <= 0.1 + 1e-9 for earlier, later in zip(ys, ys[1:]))
    assert (moves[-1].pose.y, moves[-1].pose.yaw) == (1.8, 0.3)


# --- compilation ------------------------------------------------------------------------------


def test_compile_rejects_unsupported_dynamic_events_flag():
    # Act / Assert
    with pytest.raises(ValueError, match="dynamic_events are not supported"):
        compile_sdf(Scenario(challenge={"dynamic_events": True}))


def test_compile_marks_transparent_and_reflective_materials():
    # Arrange
    scenario = Scenario(version="2", objects=[
        _box("glass", 2.0, 2.0, material="transparent"), _cylinder("mirror", -2.0, 2.0, material="reflective"),
    ])

    # Act
    root = ET.fromstring(compile_sdf(scenario).split("\n", 1)[1])

    # Assert
    visuals = {model.get("name"): model.find("link/visual") for model in root.iter("model")}
    assert visuals["obstacle_glass"].findtext("transparency") == "0.95"
    assert visuals["obstacle_glass"].findtext("laser_retro") == "20"
    assert visuals["obstacle_mirror"].find("transparency") is None
    assert visuals["obstacle_mirror"].findtext("laser_retro") == "500"
    assert visuals["obstacle_mirror"].findtext("geometry/cylinder/radius") == "0.2"


# --- maps -------------------------------------------------------------------------------------


def test_grid_dimensions_must_match_data():
    # Act / Assert
    with pytest.raises(ValueError, match="does not match grid dimensions"):
        Grid(width=2, height=2, resolution=0.1, origin=Pose(x=0, y=0), data=[0, 0, 0])


def test_distance_to_cylinder_and_rotated_box_surfaces():
    # Arrange
    pillar = _cylinder("pillar", 0.0, 0.0, radius=0.5)
    plank = ScenarioObject(id="plank", shape="box", pose=Pose(x=0, y=0, yaw=math.pi / 2),
                           size=Size(x=2.0, y=0.2))

    # Act / Assert
    assert distance_to_obstacle(2.0, 0.0, pillar) == pytest.approx(1.5)
    assert distance_to_obstacle(0.3, 0.0, pillar) == pytest.approx(-0.2)
    assert distance_to_obstacle(0.0, 1.5, plank) == pytest.approx(0.5)
    assert distance_to_obstacle(1.1, 0.0, plank) == pytest.approx(1.0)


def test_reference_grid_refuses_oversized_worlds():
    # Arrange
    huge = Scenario(world={"min_x": -600.0, "max_x": 600.0, "min_y": -600.0, "max_y": 600.0})

    # Act / Assert
    with pytest.raises(ValueError, match="too large"):
        reference_grid(huge, resolution=0.1)


def test_reference_grid_hides_short_obstacles_from_lidar_but_not_footprint():
    # Arrange
    scenario = Scenario(objects=[_box("step", 3.0, 3.0, size=1.0, height=0.1)])

    # Act
    lidar = reference_grid(scenario, resolution=0.5)
    footprint = reference_grid(scenario, resolution=0.5, lidar_height=None)

    # Assert
    assert lidar.at(*lidar.cell(3.0, 3.0)) == 0
    assert footprint.at(*footprint.cell(3.0, 3.0)) == 100


def test_unreachable_waypoint_indexes_first_blocked_leg():
    # Arrange
    world = {"min_x": -1.0, "max_x": 6.0, "min_y": -0.6, "max_y": 0.6}
    scenario = Scenario(world=world, goals=[Pose(x=2.0, y=0.0), Pose(x=5.0, y=0.0)],
                        objects=[ScenarioObject(id="gate", shape="box", pose=Pose(x=3.5, y=0.0),
                                                size=Size(x=0.1, y=1.1))])

    # Act / Assert
    assert unreachable_waypoint(scenario) == 2
    assert unreachable_waypoint(scenario.model_copy(update={"objects": []})) is None


def test_save_map_comparison_scores_estimate_against_reference(tmp_path):
    # Arrange
    scenario = Scenario(objects=[_box("crate", 3.0, 3.0, size=1.0)])
    reference = reference_grid(scenario, resolution=0.5)
    (tmp_path / "slam-map.json").write_text(reference.model_dump_json(), encoding="utf-8")

    # Act
    comparison = save_map_comparison(tmp_path, scenario)

    # Assert
    assert comparison["map_error"] == 0.0 and comparison["coverage"] == 1.0
    assert json.loads((tmp_path / "map-metrics.json").read_text()) == comparison
    assert Grid.model_validate_json((tmp_path / "reference-map.json").read_text()) == reference
    assert save_map_comparison(tmp_path / "missing", scenario) is None


# --- materials --------------------------------------------------------------------------------


def test_reference_rays_hit_cylinders_and_skip_short_or_transparent_objects():
    # Arrange
    scenario = Scenario(version="2", objects=[
        _cylinder("pillar", 2.0, 0.0, radius=0.5),
        _cylinder("stool", 0.0, 2.0, radius=0.3, height=0.1),
        _box("mat", -2.0, 0.0, height=0.05),
        _box("glass", 0.0, -2.0, material="transparent"),
    ])

    # Act
    east, north, west, south = reference_rays(
        scenario, Pose(x=0, y=0), (0.0, math.pi / 2, math.pi, -math.pi / 2))

    # Assert
    assert (east["surface"], east["range_m"]) == ("pillar", pytest.approx(1.5))
    assert (north["surface"], north["range_m"]) == ("wall", pytest.approx(5.0))
    assert (west["surface"], west["range_m"]) == ("wall", pytest.approx(5.0))
    assert (south["surface"], south["range_m"]) == ("wall", pytest.approx(5.0))


def test_reference_rays_from_inside_a_cylinder_and_outside_the_world():
    # Arrange
    scenario = Scenario(version="2", objects=[_cylinder("ring", 0.0, 0.0, radius=1.0, material="dark")])

    # Act
    inside = reference_rays(scenario, Pose(x=0, y=0), (0.0,))[0]
    outside = reference_rays(Scenario(), Pose(x=0, y=6.0), (0.0,))[0]
    behind = reference_rays(scenario, Pose(x=3.0, y=0), (0.0,))[0]

    # Assert
    assert (inside["surface"], inside["range_m"], inside["intensity"]) == ("ring", None, 5)
    assert (outside["surface"], outside["range_m"]) == ("none", None)
    assert (behind["surface"], behind["range_m"]) == ("wall", pytest.approx(2.0))


def test_reference_rays_hit_rotated_boxes_and_miss_offset_ones():
    # Arrange: a 1 m square rotated 45 degrees presents its corner sqrt(0.5) m before its centre.
    diamond = ScenarioObject(id="diamond", shape="box", pose=Pose(x=3.0, y=0.0, yaw=math.pi / 4),
                             size=Size(x=1.0, y=1.0, z=1.0))
    scenario = Scenario(objects=[diamond, _box("aside", 0.0, 3.0, size=0.4)])

    # Act
    east, diagonal = reference_rays(scenario, Pose(x=0, y=0), (0.0, math.pi / 4))

    # Assert
    assert (east["surface"], east["range_m"]) == ("diamond", pytest.approx(3.0 - math.sqrt(0.5)))
    assert diagonal["surface"] == "wall"
    assert diagonal["range_m"] == pytest.approx(5.0 * math.sqrt(2))
