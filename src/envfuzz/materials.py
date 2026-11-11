"""Passive, planar material reference model for offline simulation fixtures."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from .scenario import Pose, Scenario, ScenarioObject

Material = Literal["opaque", "transparent", "reflective", "dark"]


@dataclass(frozen=True)
class Surface:
    intensity: float
    transmits: bool = False


SURFACES: dict[Material, Surface] = {
    "opaque": Surface(120),
    "transparent": Surface(20, transmits=True),
    "reflective": Surface(500),
    "dark": Surface(5),
}
MIN_RETURN_INTENSITY = 10
SCAN_HEIGHT_M = 0.16


def _box_interval(
    x: float, y: float, dx: float, dy: float,
    xmin: float, xmax: float, ymin: float, ymax: float,
) -> tuple[float, float] | None:
    near, far = -math.inf, math.inf
    for origin, direction, low, high in ((x, dx, xmin, xmax), (y, dy, ymin, ymax)):
        if abs(direction) < 1e-12:
            if not low <= origin <= high:
                return None
            continue
        first, last = sorted(((low - origin) / direction, (high - origin) / direction))
        near, far = max(near, first), min(far, last)
        if near > far:
            return None
    return (near, far) if far >= 0 else None


def _distance(obj: ScenarioObject, x: float, y: float, dx: float, dy: float) -> float | None:
    x, y = x - obj.pose.x, y - obj.pose.y
    if obj.shape == "cylinder":
        if obj.height < SCAN_HEIGHT_M:
            return None
        assert obj.radius is not None
        along = x * dx + y * dy
        discriminant = along**2 - (x*x + y*y - obj.radius**2)
        if discriminant < 0:
            return None
        half = math.sqrt(discriminant)
        return max(0.0, -along - half) if -along + half >= 0 else None
    assert obj.size is not None
    if obj.size.z < SCAN_HEIGHT_M:
        return None
    c, s = math.cos(obj.pose.yaw), math.sin(obj.pose.yaw)
    interval = _box_interval(
        c*x + s*y, -s*x + c*y, c*dx + s*dy, -s*dx + c*dy,
        -obj.size.x/2, obj.size.x/2, -obj.size.y/2, obj.size.y/2,
    )
    return max(0.0, interval[0]) if interval else None


def reference_rays(
    scenario: Scenario, origin: Pose, angles: tuple[float, ...],
) -> list[dict[str, float | str | None]]:
    """Evaluate fixed rays through a static fixture without using ROS or altering live scans."""
    world = scenario.world
    rows = []
    for angle in angles:
        heading = origin.yaw + angle
        dx, dy = math.cos(heading), math.sin(heading)
        wall = _box_interval(
            origin.x, origin.y, dx, dy, world["min_x"], world["max_x"],
            world["min_y"], world["max_y"],
        )
        hit = (wall[1], "wall", SURFACES["opaque"]) if wall else (math.inf, "none", SURFACES["dark"])
        for obj in scenario.objects:
            surface = SURFACES[obj.material or "opaque"]
            if surface.transmits:
                continue
            distance = _distance(obj, origin.x, origin.y, dx, dy)
            if distance is not None and distance < hit[0]:
                hit = (distance, obj.id, surface)
        distance, name, surface = hit
        rows.append({
            "angle_rad": angle, "surface": name,
            "range_m": distance if surface.intensity >= MIN_RETURN_INTENSITY else None,
            "intensity": surface.intensity,
        })
    return rows
