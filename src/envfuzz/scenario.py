"""Versioned scenario models, validation, canonical serialization, and SDF compilation."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SUPPORTED_VERSION = "1"


class Pose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float
    yaw: float = 0.0


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(gt=0)
    y: float = Field(gt=0)
    z: float = Field(default=1.0, gt=0)


class ScenarioObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    shape: Literal["box", "cylinder"]
    pose: Pose
    size: Size | None = None
    radius: float | None = Field(default=None, gt=0)
    height: float = Field(default=1.0, gt=0)

    @field_validator("size")
    @classmethod
    def box_size_required(cls, value: Size | None, info: Any) -> Size | None:
        if info.data.get("shape") == "box" and value is None:
            raise ValueError("size is required for box objects")
        return value

    @field_validator("radius")
    @classmethod
    def cylinder_radius_required(cls, value: float | None, info: Any) -> float | None:
        if info.data.get("shape") == "cylinder" and value is None:
            raise ValueError("radius is required for cylinder objects")
        return value


class Zone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    polygon: list[tuple[float, float]] = Field(min_length=3)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = SUPPORTED_VERSION
    seed: int = 0
    objects: list[ScenarioObject] = Field(default_factory=list)
    start: Pose = Pose(x=0.0, y=0.0)
    goals: list[Pose] = Field(default_factory=lambda: [Pose(x=2.0, y=0.0)])
    restricted_zones: list[Zone] = Field(default_factory=list)
    challenge: dict[str, Any] = Field(default_factory=dict)
    world: dict[str, float] = Field(
        default_factory=lambda: {"min_x": -5.0, "max_x": 5.0, "min_y": -5.0, "max_y": 5.0}
    )

    @field_validator("version")
    @classmethod
    def supported_version(cls, value: str) -> str:
        if value != SUPPORTED_VERSION:
            raise ValueError(f"unsupported scenario version: {value}")
        return value

    @field_validator("objects")
    @classmethod
    def unique_ids(cls, value: list[ScenarioObject]) -> list[ScenarioObject]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("objects.id values must be unique")
        return value

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def load_scenario(path: str | Path) -> Scenario:
    source = Path(path)
    with source.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("scenario root must be a mapping")
    return Scenario.model_validate(data)


def save_scenario(scenario: Scenario, path: str | Path) -> None:
    Path(path).write_text(
        yaml.safe_dump(scenario.canonical_dict(), sort_keys=True),
        encoding="utf-8",
    )


def _radius(obj: ScenarioObject) -> float:
    if obj.shape == "cylinder":
        return obj.radius or 0.0
    size = obj.size
    return math.hypot(size.x, size.y) / 2 if size else 0.0


def _bounds(obj: ScenarioObject) -> tuple[float, float, float, float]:
    if obj.shape == "cylinder":
        radius = obj.radius or 0.0
        return (obj.pose.x - radius, obj.pose.x + radius, obj.pose.y - radius, obj.pose.y + radius)
    size = obj.size
    assert size is not None
    half_x, half_y = size.x / 2, size.y / 2
    return (obj.pose.x - half_x, obj.pose.x + half_x, obj.pose.y - half_y, obj.pose.y + half_y)


def validate_scenario(scenario: Scenario, clearance: float = 0.35) -> list[str]:
    violations: list[str] = []
    world = scenario.world
    for obj in scenario.objects:
        min_x, max_x, min_y, max_y = _bounds(obj)
        if min_x < world["min_x"] or max_x > world["max_x"] or min_y < world["min_y"] or max_y > world["max_y"]:
            violations.append(f"{obj.id}: object exceeds world bounds")
        if math.hypot(obj.pose.x - scenario.start.x, obj.pose.y - scenario.start.y) < _radius(obj) + clearance:
            violations.append(f"{obj.id}: violates start clearance")
        for goal in scenario.goals:
            if math.hypot(obj.pose.x - goal.x, obj.pose.y - goal.y) < _radius(obj) + clearance:
                violations.append(f"{obj.id}: violates goal clearance")
    for index, left in enumerate(scenario.objects):
        for right in scenario.objects[index + 1 :]:
            if math.hypot(left.pose.x - right.pose.x, left.pose.y - right.pose.y) < _radius(left) + _radius(right):
                violations.append(f"{left.id}/{right.id}: objects overlap")
    if scenario.challenge.get("forbid_restricted_objects"):
        for obj in scenario.objects:
            for zone in scenario.restricted_zones:
                if _inside(obj.pose.x, obj.pose.y, zone.polygon):
                    violations.append(f"{obj.id}: object is inside restricted zone {zone.id}")
    return violations


def _inside(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def compile_sdf(scenario: Scenario) -> str:
    violations = validate_scenario(scenario)
    if violations:
        raise ValueError("; ".join(violations))
    if scenario.challenge.get("dynamic_events"):
        raise ValueError("dynamic_events are not supported in scenario version 1")
    lines = [
        '<?xml version="1.0" ?>',
        '<sdf version="1.9">',
        '  <world name="envfuzz">',
        "    <gravity>0 0 -9.81</gravity>",
    ]
    for obj in sorted(scenario.objects, key=lambda item: item.id):
        lines.extend([f'    <model name="{obj.id}">', "      <static>true</static>"])
        if obj.shape == "box":
            size = obj.size
            assert size is not None
            geometry = f"<box><size>{size.x} {size.y} {size.z}</size></box>"
        else:
            geometry = f"<cylinder><radius>{obj.radius}</radius><length>{obj.height}</length></cylinder>"
        lines.extend([
            f"      <pose>{obj.pose.x} {obj.pose.y} 0 {obj.pose.yaw} 0 0</pose>",
            "      <link name=\"link\">",
            f"        <collision name=\"collision\"><geometry>{geometry}</geometry></collision>",
            "      </link>",
            "    </model>",
        ])
    lines.extend(["  </world>", "</sdf>", ""])
    return "\n".join(lines)


SCENARIO_JSON_SCHEMA = Scenario.model_json_schema()
