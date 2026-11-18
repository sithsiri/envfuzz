"""Unit tests for budgeted scenario edits (every operator, budget and validity rule)."""

from __future__ import annotations

import math

import pytest

from envfuzz.scenario import Pose, Scenario, ScenarioObject, Size, validate_scenario
from envfuzz.variants import (
    Edit,
    EditBudget,
    apply_edits,
    seeded_clutter_edit,
    seeded_material_edit,
)

DOOR_POSES = {
    "open": {"x": -1.0, "y": 3.0, "yaw": math.pi / 2},
    "closed": {"x": 0.0, "y": 3.0, "yaw": 0.0},
}
EVERY_OPERATOR = ["move", "rotate", "scale", "add_clutter", "remove_clutter", "door", "material"]


def _box(object_id: str, x: float, y: float, size: float = 0.4, **extra) -> ScenarioObject:
    return ScenarioObject(id=object_id, shape="box", pose=Pose(x=x, y=y),
                          size=Size(x=size, y=size, z=0.6), **extra)


@pytest.fixture
def base() -> Scenario:
    return Scenario(
        version="2",
        objects=[
            _box("chair", 2.0, 2.0),
            ScenarioObject(id="pillar", shape="cylinder", pose=Pose(x=-2.0, y=-2.0),
                           radius=0.2, height=1.0),
            ScenarioObject(id="door", shape="box", pose=Pose(**DOOR_POSES["closed"]),
                           size=Size(x=0.8, y=0.1, z=2.0)),
        ],
        challenge={"doors": {"door": DOOR_POSES}},
    )


def _objects(scenario: Scenario) -> dict[str, ScenarioObject]:
    return {obj.id: obj for obj in scenario.objects}


def test_move_updates_position_and_keeps_heading(base):
    # Arrange
    edit = Edit(operator="move", object_id="chair", pose=Pose(x=2.5, y=2.5, yaw=1.0))

    # Act
    variant = apply_edits(base, [edit], EditBudget())

    # Assert
    chair = _objects(variant)["chair"]
    assert (chair.pose.x, chair.pose.y, chair.pose.yaw) == (2.5, 2.5, 0.0)
    assert _objects(base)["chair"].pose.x == 2.0  # base scenario is not mutated


def test_cumulative_moves_beyond_displacement_budget_are_rejected(base):
    # Arrange
    edits = [
        Edit(operator="move", object_id="chair", pose=Pose(x=3.0, y=2.0)),
        Edit(operator="move", object_id="chair", pose=Pose(x=3.0, y=3.5)),
    ]

    # Act / Assert
    with pytest.raises(ValueError, match="Edit 2: exceeds an object count or motion budget"):
        apply_edits(base, edits, EditBudget(max_displacement_m=2.0))


def test_rotate_adds_to_heading_and_respects_rotation_budget(base):
    # Arrange
    turn = Edit(operator="rotate", object_id="chair", value=-0.5)

    # Act
    variant = apply_edits(base, [turn, turn], EditBudget(max_rotation_rad=1.0))

    # Assert
    assert _objects(variant)["chair"].pose.yaw == pytest.approx(-1.0)
    with pytest.raises(ValueError, match="Edit 3"):
        apply_edits(base, [turn, turn, turn], EditBudget(max_rotation_rad=1.0))


def test_scale_resizes_boxes_and_cylinders(base):
    # Arrange
    edits = [
        Edit(operator="scale", object_id="chair", value=1.5),
        Edit(operator="scale", object_id="pillar", value=0.5),
    ]

    # Act
    variant = apply_edits(base, edits, EditBudget())

    # Assert
    chair, pillar = _objects(variant)["chair"], _objects(variant)["pillar"]
    assert (chair.size.x, chair.size.y, chair.size.z) == pytest.approx((0.6, 0.6, 0.9))
    assert (pillar.radius, pillar.height) == pytest.approx((0.1, 0.5))


@pytest.mark.parametrize("value", [None, 0.2, 4.5])
def test_scale_outside_allowed_factor_range_is_invalid(base, value):
    # Arrange
    edit = Edit(operator="scale", object_id="chair", value=value)

    # Act / Assert
    with pytest.raises(ValueError, match="missing or invalid value for scale"):
        apply_edits(base, [edit], EditBudget())


def test_scale_delta_is_charged_against_budget(base):
    # Arrange
    edit = Edit(operator="scale", object_id="chair", value=2.0)

    # Act / Assert
    with pytest.raises(ValueError, match="motion budget"):
        apply_edits(base, [edit], EditBudget(max_scale_delta=0.5))


def test_add_clutter_inserts_a_new_obstacle(base):
    # Arrange
    obstacle = _box("crate", -2.0, 2.0)

    # Act
    variant = apply_edits(base, [Edit(operator="add_clutter", object_id="crate", obstacle=obstacle)],
                          EditBudget())

    # Assert
    assert _objects(variant)["crate"] == obstacle
    assert len(variant.objects) == len(base.objects) + 1


@pytest.mark.parametrize("object_id, obstacle_id, has_obstacle", [
    ("chair", "chair", True),     # duplicate ID
    ("crate", "other", True),     # mismatched ID
    ("crate", "crate", False),    # obstacle missing
])
def test_add_clutter_requires_unique_matching_obstacle(base, object_id, obstacle_id, has_obstacle):
    # Arrange
    obstacle = _box(obstacle_id, -2.0, 2.0) if has_obstacle else None
    edit = Edit(operator="add_clutter", object_id=object_id, obstacle=obstacle)

    # Act / Assert
    with pytest.raises(ValueError, match="unique matching object ID"):
        apply_edits(base, [edit], EditBudget())


def test_clutter_material_needs_material_operator_in_budget(base):
    # Arrange
    obstacle = _box("glass", -2.0, 2.0, material="transparent")
    edit = Edit(operator="add_clutter", object_id="glass", obstacle=obstacle)

    # Act
    allowed = apply_edits(base, [edit], EditBudget(allowed=EVERY_OPERATOR))

    # Assert
    assert _objects(allowed)["glass"].material == "transparent"
    with pytest.raises(ValueError, match="material is outside the budget"):
        apply_edits(base, [edit], EditBudget())


def test_object_count_budget_limits_clutter(base):
    # Arrange
    edit = Edit(operator="add_clutter", object_id="crate", obstacle=_box("crate", -2.0, 2.0))

    # Act / Assert
    with pytest.raises(ValueError, match="object count"):
        apply_edits(base, [edit], EditBudget(max_objects=len(base.objects)))


def test_remove_clutter_deletes_object_but_not_doors(base):
    # Act
    variant = apply_edits(base, [Edit(operator="remove_clutter", object_id="pillar")], EditBudget())

    # Assert
    assert "pillar" not in _objects(variant)
    with pytest.raises(ValueError, match="use the door operator"):
        apply_edits(base, [Edit(operator="remove_clutter", object_id="door")], EditBudget())


def test_door_operator_uses_trusted_pose_and_charges_motion(base):
    # Arrange
    edit = Edit(operator="door", object_id="door", door_state="open")

    # Act
    variant = apply_edits(base, [edit], EditBudget())

    # Assert
    assert _objects(variant)["door"].pose == Pose(**DOOR_POSES["open"])
    with pytest.raises(ValueError, match="motion budget"):
        apply_edits(base, [edit], EditBudget(max_rotation_rad=1.0))


def test_door_operator_rejects_objects_without_trusted_poses(base):
    # Arrange
    edit = Edit(operator="door", object_id="chair", door_state="open")

    # Act / Assert
    with pytest.raises(ValueError, match="missing trusted pose for chair"):
        apply_edits(base, [edit], EditBudget())


def test_material_operator_changes_surface(base):
    # Arrange
    edit = Edit(operator="material", object_id="chair", material="reflective")

    # Act
    variant = apply_edits(base, [edit], EditBudget(allowed=EVERY_OPERATOR))

    # Assert
    assert _objects(variant)["chair"].material == "reflective"


@pytest.mark.parametrize("operator", ["move", "rotate", "door", "material"])
def test_operator_without_its_value_is_invalid(base, operator):
    # Arrange
    edit = Edit(operator=operator, object_id="chair")

    # Act / Assert
    with pytest.raises(ValueError, match=f"missing or invalid value for {operator}"):
        apply_edits(base, [edit], EditBudget(allowed=EVERY_OPERATOR))


def test_budget_rejects_disallowed_operators_unknown_objects_and_excess_edits(base):
    # Arrange
    material = Edit(operator="material", object_id="chair", material="dark")
    ghost = Edit(operator="rotate", object_id="ghost", value=0.1)
    nudge = Edit(operator="rotate", object_id="chair", value=0.1)

    # Act / Assert
    with pytest.raises(ValueError, match="operator material is outside the budget"):
        apply_edits(base, [material], EditBudget())
    with pytest.raises(ValueError, match="unknown object ghost"):
        apply_edits(base, [ghost], EditBudget())
    with pytest.raises(ValueError, match="Edit count exceeds"):
        apply_edits(base, [nudge] * 3, EditBudget(max_edits=2))


def test_edit_that_breaks_validity_names_the_edit_and_violation(base):
    # Arrange
    onto_goal = Edit(operator="move", object_id="chair", pose=Pose(x=2.0, y=0.3))

    # Act / Assert
    with pytest.raises(ValueError, match=r"Edit 1: chair: violates goal clearance"):
        apply_edits(base, [onto_goal], EditBudget())


def test_empty_edit_list_still_validates_the_base(base):
    # Arrange
    invalid = base.model_copy(update={"objects": [_box("blocker", 0.0, 0.0)]})

    # Act / Assert
    assert apply_edits(base, [], EditBudget()) == base
    with pytest.raises(ValueError, match="blocker: violates start clearance"):
        apply_edits(invalid, [], EditBudget())


def test_seeded_clutter_edit_is_deterministic_and_inside_world(base):
    # Act
    first = seeded_clutter_edit(base, seed=11, object_id="crate")
    again = seeded_clutter_edit(base, seed=11, object_id="crate")
    other = seeded_clutter_edit(base, seed=12, object_id="crate")

    # Assert
    assert first == again
    assert first.obstacle.pose != other.obstacle.pose
    assert first.operator == "add_clutter" and first.obstacle.id == "crate"
    assert -5 <= first.obstacle.pose.x <= 5 and -5 <= first.obstacle.pose.y <= 5


def test_seeded_material_edit_always_changes_the_material(base):
    # Arrange
    glass = base.model_copy(update={"objects": [*base.objects[:2], base.objects[2].model_copy(
        update={"material": "transparent"})]})

    # Act
    choices = {seeded_material_edit(glass, seed, "door").material for seed in range(40)}

    # Assert
    assert choices == {"opaque", "reflective", "dark"}
    assert seeded_material_edit(base, 5, "chair") == seeded_material_edit(base, 5, "chair")
    assert "opaque" not in {seeded_material_edit(base, seed, "chair").material for seed in range(40)}
    with pytest.raises(ValueError, match="Unknown material edit target"):
        seeded_material_edit(base, 1, "ghost")


def test_seeded_material_edit_applies_under_material_budget(base):
    # Arrange
    edit = seeded_material_edit(base, seed=3, object_id="pillar")

    # Act
    variant = apply_edits(base, [edit], EditBudget(allowed=EVERY_OPERATOR))

    # Assert
    assert _objects(variant)["pillar"].material == edit.material
    assert validate_scenario(variant) == []
