"""Reproducible navigation scenarios for comparing sampling methods."""

from __future__ import annotations

from collections import deque
from itertools import cycle, product
import math
import random
from typing import Literal

from .scenario import Pose, Scenario, ScenarioObject, Size, validate_scenario

Layout = Literal["apartment", "corridor", "open_room"]
Sampling = Literal["uniform", "stratified"]
LAYOUT_DEPTHS = {"apartment": (5.5, 6.5, 7.5), "corridor": (2.5, 3.0, 3.5),
                 "open_room": (8.0, 9.0, 10.0)}
STRATA = tuple(product(range(3), repeat=3))


def _distance_to_obstacle(x: float, y: float, obstacle: ScenarioObject) -> float:
    dx, dy = x - obstacle.pose.x, y - obstacle.pose.y
    if obstacle.shape == "cylinder":
        return math.hypot(dx, dy) - obstacle.radius
    c, s = math.cos(obstacle.pose.yaw), math.sin(obstacle.pose.yaw)
    local_x, local_y = dx * c + dy * s, -dx * s + dy * c
    return math.hypot(
        max(0.0, abs(local_x) - obstacle.size.x / 2),
        max(0.0, abs(local_y) - obstacle.size.y / 2),
    )


def route_exists(scenario: Scenario, resolution: float = 0.2, radius: float = 0.22) -> bool:
    """Check waypoint connectivity on a grid conservatively inflated by the footprint."""
    bounds = scenario.world
    min_x, min_y = bounds["min_x"], bounds["min_y"]
    width = math.floor((bounds["max_x"] - min_x) / resolution)
    height = math.floor((bounds["max_y"] - min_y) / resolution)
    if width * height > 100_000:
        raise ValueError("World is too large for the reachability grid")
    margin = radius + resolution / math.sqrt(2)
    free = set()
    for i in range(width):
        x = min_x + (i + 0.5) * resolution
        for j in range(height):
            y = min_y + (j + 0.5) * resolution
            wall_distance = min(x - min_x, bounds["max_x"] - x, y - min_y, bounds["max_y"] - y)
            if wall_distance >= margin and all(
                _distance_to_obstacle(x, y, obstacle) >= margin for obstacle in scenario.objects
            ):
                free.add((i, j))

    waypoints = [
        (math.floor((pose.x - min_x) / resolution), math.floor((pose.y - min_y) / resolution))
        for pose in [scenario.start, *scenario.goals]
    ]
    if not all(point in free for point in waypoints):
        return False
    reachable = {waypoints[0]}
    pending = deque(reachable)
    while pending:
        x, y = pending.popleft()
        for point in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if point in free and point not in reachable:
                reachable.add(point)
                pending.append(point)
    return all(point in reachable for point in waypoints[1:])


def sample_scenarios(layout: Layout, method: Sampling, seed: int, count: int) -> list[Scenario]:
    rng = random.Random(seed)
    strata = list(STRATA)
    rng.shuffle(strata)
    balanced = cycle(strata)
    scenarios = []
    for _ in range(count):
        width, density, distance = next(balanced) if method == "stratified" else rng.choice(STRATA)
        for _ in range(50):
            scenario = _draw_scenario(layout, seed, rng, width, density, distance)
            if scenario is not None and route_exists(scenario):
                scenarios.append(scenario)
                break
        else:
            raise ValueError(f"Could not sample a reachable {layout} scenario in {(width, density, distance)}")
    return scenarios


def _draw_scenario(
    layout: Layout, seed: int, rng: random.Random, width: int, density: int, distance: int,
) -> Scenario | None:
    half_depth = LAYOUT_DEPTHS[layout][width] / 2
    travel = (2.5, 4.0, 6.0)[distance]
    clutter = (2, 4, 6)[density]
    scenario = Scenario(
        seed=seed,
        start=Pose(x=-3.5, y=rng.uniform(-0.35, 0.35)),
        goals=[Pose(x=-3.5 + travel, y=rng.uniform(-0.35, 0.35))],
        world={"min_x": -5, "max_x": 5, "min_y": -half_depth, "max_y": half_depth},
        challenge={"layout": layout, "width_bin": width, "clutter_count": clutter,
                   "goal_distance_bin": distance},
    )
    if layout == "apartment":
        scenario.objects.append(ScenarioObject(
            id="partition", shape="box", pose=Pose(x=0, y=half_depth - 0.8),
            size=Size(x=0.15, y=1.6, z=2.0),
        ))
    for index in range(clutter):
        for _ in range(40):
            scenario.objects.append(ScenarioObject(
                id=f"clutter-{index + 1}", shape="box",
                pose=Pose(x=rng.uniform(-4.5, 4.5), y=rng.uniform(-half_depth + 0.5, half_depth - 0.5),
                          yaw=rng.uniform(-math.pi, math.pi)),
                size=Size(x=rng.uniform(0.25, 0.7), y=rng.uniform(0.25, 0.7), z=0.65),
            ))
            if not validate_scenario(scenario):
                break
            scenario.objects.pop()
        else:
            return None
    return scenario
