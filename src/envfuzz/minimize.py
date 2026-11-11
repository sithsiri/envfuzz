"""Reduce a saved navigation regression while measuring repeatability."""

from __future__ import annotations

import json
from pathlib import Path

from .analysis import regression_signature
from .gazebo import run_gazebo
from .scenario import load_scenario, save_scenario, validate_scenario


def minimize_run(bundle: Path, output: Path, repeats: int, required_rate: float) -> dict:
    original = load_scenario(bundle / "scenario.json")
    layout = original.challenge.get("layout", "custom")
    signature = regression_signature(bundle, layout)
    if signature is None:
        raise ValueError("The run has no recorded navigation or mapping regression")
    config = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    options = [
        path if path.is_file() else None for path in
        (bundle / "nav2.yaml", bundle / "baseline.json", bundle / "oracle.yaml", bundle / "profile.yaml")
    ]
    output.mkdir(parents=True, exist_ok=False)
    attempts = []

    def reproduce(scenario, label):
        matching = valid = 0
        bundles = []
        for index in range(1, repeats + 1):
            trial = output / label / f"repeat-{index:03d}"
            result = run_gazebo(scenario, trial, config["timeout"], config["watchdog"], *options)
            bundles.append(trial.relative_to(output).as_posix())
            if result.classification == "behavior":
                valid += 1
                matching += int(regression_signature(trial, layout) == signature)
        outcome = {"candidate": label, "matching": matching, "valid": valid, "attempted": repeats,
                   "bundles": bundles, "rate": matching / valid if valid else 0.0}
        attempts.append(outcome)
        return outcome

    current = original
    best = reproduce(current, "original")
    if best["valid"] != repeats or best["rate"] < required_rate:
        raise ValueError("Original run did not reproduce at the required rate")
    timed = {event.object_id for event in original.events or []}
    for index, obstacle in enumerate(original.objects, 1):
        if obstacle.id in timed:
            continue
        variant = current.model_copy(update={"objects": [obj for obj in current.objects if obj.id != obstacle.id]})
        if validate_scenario(variant):
            continue
        outcome = reproduce(variant, f"candidate-{index:03d}")
        if outcome["valid"] == repeats and outcome["rate"] >= required_rate:
            current, best = variant, outcome
    save_scenario(current, output / "scenario.yaml")
    report = {"original_bundle": str(bundle.resolve()), "signature": signature, "selected": best,
              "original_objects": len(original.objects), "remaining_objects": len(current.objects),
              "candidates": attempts}
    (output / "minimization.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
