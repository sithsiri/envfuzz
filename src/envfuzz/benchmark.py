"""Reproducible navigation scenarios for comparing sampling methods."""

from __future__ import annotations

from itertools import cycle, product
import math
import random
from typing import Literal

from .maps import unreachable_waypoint
from .scenario import Pose, Scenario, ScenarioObject, Size, validate_scenario

Layout = Literal["apartment", "corridor", "open_room"]
Sampling = Literal["uniform", "stratified", "coverage"]
LAYOUT_DEPTHS = {"apartment": (5.5, 6.5, 7.5), "corridor": (2.5, 3.0, 3.5),
                 "open_room": (8.0, 9.0, 10.0)}
STRATA = tuple(product(range(3), repeat=3))


def route_exists(scenario: Scenario, resolution: float = 0.2, radius: float = 0.22) -> bool:
    return unreachable_waypoint(scenario, resolution, radius) is None


def sample_scenarios(layout: Layout, method: Sampling, seed: int, count: int) -> list[Scenario]:
    rng = random.Random(seed)
    strata = list(STRATA)
    rng.shuffle(strata)
    balanced = cycle(strata)
    scenarios = []
    counts = dict.fromkeys(STRATA, 0)
    for _ in range(count):
        if method == "stratified":
            width, density, distance = next(balanced)
        elif method == "coverage":
            candidates = rng.sample(STRATA, 5)
            width, density, distance = min(candidates, key=counts.__getitem__)
        else:
            width, density, distance = rng.choice(STRATA)
        for _ in range(50):
            scenario = _draw_scenario(layout, seed, rng, width, density, distance)
            if scenario is not None and route_exists(scenario):
                scenarios.append(scenario)
                counts[width, density, distance] += 1
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
