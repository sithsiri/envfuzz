"""Versioned scenario models, validation, canonical serialization, and SDF compilation."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .profile import SimulationProfile

SUPPORTED_VERSION = "1"


class Pose(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float
    y: float
    yaw: float = 0.0


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(gt=0)
    y: float = Field(gt=0)
    z: float = Field(default=1.0, gt=0)


class ScenarioObject(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    shape: Literal["box", "cylinder"]
    pose: Pose
    size: Size | None = Field(default=None, validate_default=True)
    radius: float | None = Field(default=None, gt=0, validate_default=True)
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


class TimedMove(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    at: float = Field(ge=0, le=3600)
    object_id: str
    pose: Pose


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
    events: list[TimedMove] | None = Field(default=None, max_length=1000)
    surface_friction: dict[str, float] | None = None

    @field_validator("version")
    @classmethod
    def supported_version(cls, value: str) -> str:
        if value not in {SUPPORTED_VERSION, "2"}:
            raise ValueError(f"unsupported scenario version: {value}")
        return value

    @field_validator("events", "surface_friction")
    @classmethod
    def versioned_variants(cls, value, info: Any):
        if value is not None and info.data.get("version") != "2":
            raise ValueError(f"{info.field_name} requires scenario version 2")
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
    cosine, sine = abs(math.cos(obj.pose.yaw)), abs(math.sin(obj.pose.yaw))
    half_x = (size.x * cosine + size.y * sine) / 2
    half_y = (size.x * sine + size.y * cosine) / 2
    return (obj.pose.x - half_x, obj.pose.x + half_x, obj.pose.y - half_y, obj.pose.y + half_y)


def validate_scenario(scenario: Scenario, clearance: float = 0.35) -> list[str]:
    violations: list[str] = []
    world = scenario.world
    for axis in ("x", "y"):
        lower = world.get(f"min_{axis}")
        upper = world.get(f"max_{axis}")
        if lower is None or upper is None or not (
            math.isfinite(lower) and math.isfinite(upper) and lower < upper
        ):
            violations.append(f"world: min_{axis} and max_{axis} must be finite and increasing")
    if violations:
        return violations
    mission_poses = [("start", scenario.start)]
    mission_poses.extend((f"goal {index}", goal) for index, goal in enumerate(scenario.goals, 1))
    for name, pose in mission_poses:
        if not (
            world["min_x"] + clearance <= pose.x <= world["max_x"] - clearance
            and world["min_y"] + clearance <= pose.y <= world["max_y"] - clearance
        ):
            violations.append(f"{name}: violates wall clearance")
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
    if not violations and scenario.challenge.get("require_reachable"):
        from .maps import unreachable_waypoint

        unreachable = unreachable_waypoint(scenario)
        if unreachable is not None:
            label = f"goal {unreachable}" if unreachable else "start"
            violations.append(f"{label}: no route with robot footprint clearance")
    if not violations and scenario.events:
        objects = {obj.id: obj for obj in scenario.objects}
        previous = -1.0
        for index, event in enumerate(scenario.events, 1):
            if event.at < previous or event.object_id not in objects:
                violations.append(f"event {index}: order or object reference is invalid")
                break
            previous = event.at
            objects[event.object_id] = objects[event.object_id].model_copy(update={"pose": event.pose})
            variant = scenario.model_copy(update={"objects": list(objects.values()), "events": None})
            violations.extend(f"event {index}: {issue}" for issue in validate_scenario(variant, clearance))
            if violations:
                break
    return violations


def _inside(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def compile_sdf(scenario: Scenario, profile: SimulationProfile | None = None) -> str:
    violations = validate_scenario(scenario)
    if violations:
        raise ValueError("; ".join(violations))
    if scenario.challenge.get("dynamic_events"):
        raise ValueError("dynamic_events are not supported in scenario version 1")
    sdf = ET.Element("sdf", version="1.9")
    world = ET.SubElement(sdf, "world", name="envfuzz")
    ET.SubElement(world, "gravity").text = "0 0 -9.81"
    if profile is not None:
        physics = ET.SubElement(world, "physics", name="envfuzz_profile", type="ignored")
        ET.SubElement(physics, "max_step_size").text = str(profile.step_s)
        ET.SubElement(physics, "real_time_factor").text = str(profile.real_time_factor)
    for system, class_name in (
        ("physics", "Physics"),
        ("user-commands", "UserCommands"),
        ("sensors", "Sensors"),
        ("imu", "Imu"),
    ):
        plugin = ET.SubElement(
            world, "plugin", filename=f"gz-sim-{system}-system",
            name=f"gz::sim::systems::{class_name}",
        )
        if system == "sensors":
            ET.SubElement(plugin, "render_engine").text = "ogre2"

    bounds = scenario.world
    min_x, max_x = bounds["min_x"], bounds["max_x"]
    min_y, max_y = bounds["min_y"], bounds["max_y"]
    width, depth = max_x - min_x, max_y - min_y
    center_x, center_y = min_x + width / 2, min_y + depth / 2
    thickness, wall_height = 0.1, 2.5
    margin = thickness / 2
    room_boxes = (
        ("floor", (center_x, center_y, -margin), (width + 2 * thickness, depth + 2 * thickness, thickness)),
        ("wall_west", (min_x - margin, center_y, wall_height / 2), (thickness, depth, wall_height)),
        ("wall_east", (max_x + margin, center_y, wall_height / 2), (thickness, depth, wall_height)),
        ("wall_south", (center_x, min_y - margin, wall_height / 2), (width + 2 * thickness, thickness, wall_height)),
        ("wall_north", (center_x, max_y + margin, wall_height / 2), (width + 2 * thickness, thickness, wall_height)),
    )
    for name, position, size in room_boxes:
        _add_static_model(world, name, (*position, 0, 0, 0), "box", {"size": _numbers(size)})

    for obj in sorted(scenario.objects, key=lambda item: item.id):
        if obj.shape == "box":
            size = obj.size
            assert size is not None
            height = size.z
            dimensions = {"size": _numbers((size.x, size.y, height))}
        else:
            height = obj.height
            dimensions = {"radius": str(obj.radius), "length": str(height)}
        pose = (obj.pose.x, obj.pose.y, height / 2, 0, 0, obj.pose.yaw)
        _add_static_model(world, f"obstacle_{obj.id}", pose, obj.shape, dimensions)

    if scenario.events:
        plugin = ET.SubElement(world, "plugin", filename="libenvfuzz_timeline.so", name="envfuzz::Timeline")
        heights = {obj.id: (obj.size.z if obj.shape == "box" else obj.height) for obj in scenario.objects}
        for index, event in enumerate(scenario.events, 1):
            move = ET.SubElement(plugin, "move")
            ET.SubElement(move, "index").text = str(index)
            ET.SubElement(move, "nanoseconds").text = str(round(event.at * 1e9))
            ET.SubElement(move, "model").text = f"obstacle_{event.object_id}"
            ET.SubElement(move, "pose").text = _numbers(
                (event.pose.x, event.pose.y, heights[event.object_id] / 2, 0, 0, event.pose.yaw)
            )

    ET.indent(sdf, space="  ")
    return '<?xml version="1.0" ?>\n' + ET.tostring(sdf, encoding="unicode") + "\n"


def _numbers(values: tuple[float, ...]) -> str:
    return " ".join(str(value) for value in values)


def _add_static_model(
    world: ET.Element, name: str, pose: tuple[float, ...],
    shape: str, dimensions: dict[str, str],
) -> None:
    model = ET.SubElement(world, "model", name=name)
    ET.SubElement(model, "static").text = "true"
    ET.SubElement(model, "pose").text = _numbers(pose)
    link = ET.SubElement(model, "link", name="link")
    # Rendering-based LiDAR sees visuals while physics uses collision geometry.
    for kind in ("collision", "visual"):
        element = ET.SubElement(link, kind, name=kind)
        geometry = ET.SubElement(ET.SubElement(element, "geometry"), shape)
        for dimension, value in dimensions.items():
            ET.SubElement(geometry, dimension).text = value


SCENARIO_JSON_SCHEMA = Scenario.model_json_schema()
