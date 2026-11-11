"""Apply validated, budgeted edits to a static scenario fixture."""

from __future__ import annotations

import math
import random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .scenario import Pose, Scenario, ScenarioObject, Size, validate_scenario

Operator = Literal["move", "rotate", "scale", "add_clutter", "remove_clutter", "door"]


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    operator: Operator
    object_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=80)
    pose: Pose | None = None
    value: float | None = None
    obstacle: ScenarioObject | None = None
    door_state: Literal["open", "closed"] | None = None


class EditBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    allowed: list[Operator] = Field(default_factory=lambda: [
        "move", "rotate", "scale", "add_clutter", "remove_clutter", "door",
    ])
    max_edits: int = Field(default=5, ge=0, le=100)
    max_objects: int = Field(default=100, ge=1, le=1000)
    max_displacement_m: float = Field(default=2.0, ge=0)
    max_rotation_rad: float = Field(default=math.pi, ge=0)
    max_scale_delta: float = Field(default=1, ge=0)


def apply_edits(base: Scenario, edits: list[Edit], budget: EditBudget) -> Scenario:
    if len(edits) > budget.max_edits:
        raise ValueError("Edit count exceeds the configured budget")
    objects = {obj.id: obj for obj in base.objects}
    displacement = rotation = scale = 0.0
    for index, edit in enumerate(edits, 1):
        if edit.operator not in budget.allowed:
            raise ValueError(f"Edit {index}: operator {edit.operator} is outside the budget")
        existing = objects.get(edit.object_id)
        if edit.operator == "add_clutter":
            if existing is not None or edit.obstacle is None or edit.obstacle.id != edit.object_id:
                raise ValueError(f"Edit {index}: clutter requires a unique matching object ID")
            objects[edit.object_id] = edit.obstacle
        elif existing is None:
            raise ValueError(f"Edit {index}: unknown object {edit.object_id}")
        elif edit.operator == "remove_clutter":
            if edit.object_id in base.challenge.get("doors", {}):
                raise ValueError(f"Edit {index}: use the door operator for a door")
            del objects[edit.object_id]
        elif edit.operator == "move" and edit.pose is not None:
            displacement += math.hypot(edit.pose.x - existing.pose.x, edit.pose.y - existing.pose.y)
            pose = existing.pose.model_copy(update={"x": edit.pose.x, "y": edit.pose.y})
            objects[edit.object_id] = existing.model_copy(update={"pose": pose})
        elif edit.operator == "rotate" and edit.value is not None:
            rotation += abs(edit.value)
            pose = existing.pose.model_copy(update={"yaw": existing.pose.yaw + edit.value})
            objects[edit.object_id] = existing.model_copy(update={"pose": pose})
        elif edit.operator == "scale" and edit.value is not None and 0.25 <= edit.value <= 4:
            scale += abs(edit.value - 1)
            if existing.shape == "box":
                size = Size(x=existing.size.x * edit.value, y=existing.size.y * edit.value,
                            z=existing.size.z * edit.value)
                update = {"size": size}
            else:
                update = {"radius": existing.radius * edit.value, "height": existing.height * edit.value}
            objects[edit.object_id] = existing.model_copy(update=update)
        elif edit.operator == "door" and edit.door_state is not None:
            poses = base.challenge.get("doors", {}).get(edit.object_id, {})
            if edit.door_state not in poses:
                raise ValueError(f"Edit {index}: missing trusted pose for {edit.object_id}")
            pose = Pose.model_validate(poses[edit.door_state])
            displacement += math.hypot(pose.x - existing.pose.x, pose.y - existing.pose.y)
            rotation += abs(math.remainder(pose.yaw - existing.pose.yaw, 2 * math.pi))
            objects[edit.object_id] = existing.model_copy(update={"pose": pose})
        else:
            raise ValueError(f"Edit {index}: missing or invalid value for {edit.operator}")
        if (len(objects) > budget.max_objects or displacement > budget.max_displacement_m
                or rotation > budget.max_rotation_rad or scale > budget.max_scale_delta):
            raise ValueError(f"Edit {index}: exceeds an object count or motion budget")
        variant = base.model_copy(update={"objects": list(objects.values())})
        violations = validate_scenario(variant)
        if violations:
            raise ValueError(f"Edit {index}: {', '.join(violations)}")
    variant = base.model_copy(update={"objects": list(objects.values())})
    violations = validate_scenario(variant)
    if violations:
        raise ValueError(", ".join(violations))
    return variant


def seeded_clutter_edit(base: Scenario, seed: int, object_id: str) -> Edit:
    rng = random.Random(seed)
    world = base.world
    obstacle = ScenarioObject(
        id=object_id, shape="box", size=Size(x=0.4, y=0.4, z=0.6),
        pose=Pose(x=rng.uniform(world["min_x"], world["max_x"]),
                  y=rng.uniform(world["min_y"], world["max_y"])),
    )
    return Edit(operator="add_clutter", object_id=object_id, obstacle=obstacle)
