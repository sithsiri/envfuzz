"""Reference occupancy, map alignment, and footprint-aware reachability."""

from __future__ import annotations

from heapq import heappop, heappush
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .scenario import Pose, Scenario, ScenarioObject


class Grid(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    resolution: float = Field(gt=0)
    origin: Pose
    data: list[Literal[-1, 0, 100]] = Field(max_length=1_000_000)

    @model_validator(mode="after")
    def dimensions_match(self) -> Grid:
        if self.width * self.height != len(self.data):
            raise ValueError("Occupancy data length does not match grid dimensions")
        return self

    def cell(self, x: float, y: float) -> tuple[int, int]:
        dx, dy = x - self.origin.x, y - self.origin.y
        c, s = math.cos(self.origin.yaw), math.sin(self.origin.yaw)
        return math.floor((dx * c + dy * s) / self.resolution), math.floor((-dx * s + dy * c) / self.resolution)

    def center(self, i: int, j: int) -> tuple[float, float]:
        x, y = (i + 0.5) * self.resolution, (j + 0.5) * self.resolution
        c, s = math.cos(self.origin.yaw), math.sin(self.origin.yaw)
        return self.origin.x + x * c - y * s, self.origin.y + x * s + y * c

    def at(self, i: int, j: int) -> int:
        return self.data[j * self.width + i] if 0 <= i < self.width and 0 <= j < self.height else -1


def distance_to_obstacle(x: float, y: float, obstacle: ScenarioObject) -> float:
    dx, dy = x - obstacle.pose.x, y - obstacle.pose.y
    if obstacle.shape == "cylinder":
        return math.hypot(dx, dy) - obstacle.radius
    c, s = math.cos(obstacle.pose.yaw), math.sin(obstacle.pose.yaw)
    return math.hypot(max(0.0, abs(dx * c + dy * s) - obstacle.size.x / 2),
                      max(0.0, abs(-dx * s + dy * c) - obstacle.size.y / 2))


def reference_grid(
    scenario: Scenario, resolution: float = 0.1, lidar_height: float | None = 0.16,
    radius: float = 0.0,
) -> Grid:
    bounds = scenario.world
    min_x, min_y = bounds["min_x"], bounds["min_y"]
    max_x, max_y = bounds["max_x"], bounds["max_y"]
    padding = 0.1 if lidar_height is not None else 0.0
    width = math.ceil((max_x - min_x + 2 * padding) / resolution)
    height = math.ceil((max_y - min_y + 2 * padding) / resolution)
    if width * height > 1_000_000:
        raise ValueError("World is too large for the selected grid resolution")
    origin = Pose(x=min_x - padding, y=min_y - padding)
    obstacles = [
        obj for obj in scenario.objects
        if lidar_height is None or (obj.size.z if obj.shape == "box" else obj.height) >= lidar_height
    ]
    drops = (scenario.floor_drops or []) if lidar_height is None else []
    margin = radius + resolution / math.sqrt(2) if radius else 0
    data = []
    for j in range(height):
        y = origin.y + (j + 0.5) * resolution
        for i in range(width):
            x = origin.x + (i + 0.5) * resolution
            wall = min(x - min_x, max_x - x, y - min_y, max_y - y)
            blocked = wall < margin or any(distance_to_obstacle(x, y, obj) <= margin for obj in obstacles)
            if not blocked:
                blocked = any(drop.distance(x, y) <= margin for drop in drops)
            data.append(100 if blocked else 0)
    return Grid(width=width, height=height, resolution=resolution, origin=origin, data=data)


def compare_grids(reference: Grid, estimate: Grid) -> dict[str, float | int | None]:
    known = intersection = union = missing = phantom = 0
    for j in range(reference.height):
        for i in range(reference.width):
            truth = reference.at(i, j)
            observed = estimate.at(*estimate.cell(*reference.center(i, j)))
            if truth == -1 or observed == -1:
                continue
            known += 1
            a, b = truth == 100, observed == 100
            intersection += int(a and b)
            union += int(a or b)
            missing += int(a and not b)
            phantom += int(b and not a)
    reference_known = sum(value != -1 for value in reference.data)
    iou = (intersection / union if union else 1.0) if known else None
    return {"occupied_iou": iou, "map_error": 1 - iou if iou is not None else None,
            "coverage": known / reference_known if reference_known else 0.0,
            "compared_cells": known, "missing_occupied": missing, "phantom_occupied": phantom}


def unreachable_waypoint(scenario: Scenario, resolution: float = 0.2, radius: float = 0.22) -> int | None:
    grid = reference_grid(scenario, resolution, lidar_height=None, radius=radius)
    waypoints = [grid.cell(pose.x, pose.y) for pose in [scenario.start, *scenario.goals]]
    if grid.at(*waypoints[0]) != 0:
        return 0
    for index, (start, goal) in enumerate(zip(waypoints, waypoints[1:]), 1):
        if grid.at(*goal) != 0 or not _reachable(grid, start, goal):
            return index
    return None


def _reachable(grid: Grid, start: tuple[int, int], goal: tuple[int, int]) -> bool:
    distances = {start: 0}
    pending = [(0, 0, start)]
    while pending:
        _, distance, point = heappop(pending)
        if point == goal:
            return True
        if distance != distances[point]:
            continue
        x, y = point
        for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            candidate = distance + 1
            if grid.at(*neighbor) != 0 or candidate >= distances.get(neighbor, math.inf):
                continue
            distances[neighbor] = candidate
            heuristic = abs(goal[0] - neighbor[0]) + abs(goal[1] - neighbor[1])
            heappush(pending, (candidate + heuristic, candidate, neighbor))
    return False


def save_map_comparison(bundle: Path, scenario: Scenario) -> dict | None:
    estimate_path = bundle / "slam-map.json"
    if not estimate_path.is_file():
        return None
    estimate = Grid.model_validate_json(estimate_path.read_text(encoding="utf-8"))
    reference = reference_grid(scenario, resolution=estimate.resolution)
    comparison = compare_grids(reference, estimate)
    (bundle / "reference-map.json").write_text(reference.model_dump_json(), encoding="utf-8")
    (bundle / "map-metrics.json").write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
    return comparison


def export_navigation_map(scenario: Scenario, directory: Path, resolution: float = 0.05) -> Path:
    """Write a deterministic Nav2 map-server image and metadata from static geometry."""
    grid = reference_grid(scenario, resolution=resolution)
    image = directory / "localization-map.pgm"
    # Nav2's map_server reads PGM rows from top to bottom while Grid stores y upward.
    pixels = bytearray()
    for row in range(grid.height - 1, -1, -1):
        for column in range(grid.width):
            pixels.append(0 if grid.at(column, row) == 100 else 254)
    image.write_bytes(f"P5\n{grid.width} {grid.height}\n255\n".encode() + pixels)
    metadata = directory / "localization-map.yaml"
    metadata.write_text(
        "image: localization-map.pgm\n"
        f"resolution: {grid.resolution}\n"
        f"origin: [{grid.origin.x}, {grid.origin.y}, {grid.origin.yaw}]\n"
        "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n",
        encoding="utf-8",
    )
    return metadata


def export_keepout_filter_map(scenario: Scenario, directory: Path, resolution: float = 0.05) -> Path:
    """Write a Nav2 filter mask aligned with the deterministic navigation map."""
    from .scenario import _inside

    if not scenario.restricted_zones:
        raise ValueError("Keepout Filter requires at least one restricted zone")
    grid = reference_grid(scenario, resolution=resolution)
    image = directory / "keepout-filter.pgm"
    pixels = bytearray()
    for row in range(grid.height - 1, -1, -1):
        for column in range(grid.width):
            x, y = grid.center(column, row)
            blocked = any(_inside(x, y, zone.polygon) for zone in scenario.restricted_zones)
            pixels.append(0 if blocked else 254)
    image.write_bytes(f"P5\n{grid.width} {grid.height}\n255\n".encode() + pixels)
    metadata = directory / "keepout-filter.yaml"
    metadata.write_text(
        "image: keepout-filter.pgm\n"
        f"resolution: {grid.resolution}\n"
        f"origin: [{grid.origin.x}, {grid.origin.y}, {grid.origin.yaw}]\n"
        "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n",
        encoding="utf-8",
    )
    return metadata
