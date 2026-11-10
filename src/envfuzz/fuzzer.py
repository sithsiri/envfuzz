"""Seeded candidate generation and sequential fuzz budget accounting."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

from .results import EpisodeResult
from .scenario import Pose, Scenario, ScenarioObject, Size, validate_scenario


def candidates(seed: int, base: Scenario | None = None):
    rng = random.Random(seed)
    base = base if base is not None else Scenario()
    used_ids = {obstacle.id for obstacle in base.objects}
    index = 0
    while True:
        index += 1
        if f"object-{index}" in used_ids:
            continue
        obstacle = ScenarioObject(
            id=f"object-{index}", shape="box",
            pose=Pose(x=rng.uniform(-3.5, 3.5), y=rng.uniform(-3.5, 3.5)),
            size=Size(x=0.4, y=0.4, z=0.6),
        )
        yield base.model_copy(update={"seed": seed, "objects": [*base.objects, obstacle]})


def fuzz(
    budget: int, seed: int, run: Callable[[Scenario], EpisodeResult],
    output: str | Path = "runs/results.jsonl",
    base: Scenario | None = None,
) -> tuple[list[EpisodeResult], int]:
    if budget < 0:
        raise ValueError("budget must be non-negative")
    results: list[EpisodeResult] = []
    rejected = 0
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        if budget == 0:
            return results, rejected
        consecutive_rejections = 0
        for scenario in candidates(seed, base):
            violations = validate_scenario(scenario)
            if violations:
                rejected += 1
                consecutive_rejections += 1
                if consecutive_rejections >= 1000:
                    raise ValueError("No valid candidate found after 1000 consecutive placements")
                continue
            consecutive_rejections = 0
            result = run(scenario)
            results.append(result)
            handle.write(result.model_dump_json() + "\n")
            handle.flush()
            if result.classification != "behavior" or len(results) >= budget:
                break
    return results, rejected
