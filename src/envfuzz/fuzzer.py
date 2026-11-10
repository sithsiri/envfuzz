"""Seeded candidate generation and sequential fuzz budget accounting."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Callable

from .results import EpisodeResult
from .scenario import Pose, Scenario, ScenarioObject, Size, validate_scenario


def candidates(seed: int):
    rng = random.Random(seed)
    index = 0
    while True:
        index += 1
        yield Scenario(seed=seed, objects=[ScenarioObject(
            id=f"object-{index}", shape="box",
            pose=Pose(x=rng.uniform(-3.5, 3.5), y=rng.uniform(-3.5, 3.5)),
            size=Size(x=0.4, y=0.4, z=0.6),
        )])


def fuzz(
    budget: int, seed: int, run: Callable[[Scenario], EpisodeResult],
    output: str | Path = "runs/results.jsonl",
) -> tuple[list[EpisodeResult], int]:
    if budget < 0:
        raise ValueError("budget must be non-negative")
    results: list[EpisodeResult] = []
    rejected = 0
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for scenario in candidates(seed):
            violations = validate_scenario(scenario)
            if violations:
                rejected += 1
                continue
            result = run(scenario)
            results.append(result)
            handle.write(result.model_dump_json() + "\n")
            handle.flush()
            if len(results) >= budget:
                break
    return results, rejected
