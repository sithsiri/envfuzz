"""Plan and resume sequential simulation campaigns from a YAML manifest."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .benchmark import Layout, Sampling, sample_scenarios
from .gazebo import run_gazebo
from .results import EpisodeResult
from .scenario import Scenario


class Campaign(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    layouts: list[Layout] = Field(min_length=1)
    methods: list[Sampling] = Field(min_length=1)
    seeds: list[int] = Field(min_length=1)
    episodes_per_cell: int = Field(ge=1)
    timeout: float = Field(default=120, gt=0)
    watchdog: float = Field(default=300, gt=0)


class Case(BaseModel):
    layout: Layout
    method: Sampling
    scenario: Scenario


def prepare_campaign(path: Path, output: Path) -> tuple[Campaign, list[Case]]:
    manifest = Campaign.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if any(len(values) != len(set(values)) for values in (manifest.layouts, manifest.methods, manifest.seeds)):
        raise ValueError("Campaign layouts, methods and seeds must be unique")
    plan_path = output / "plan.json"
    if plan_path.is_file():
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        if saved["manifest"] != manifest.model_dump(mode="json"):
            raise ValueError("This output directory contains a different campaign manifest")
        return manifest, [Case.model_validate(row) for row in saved["cases"]]
    cases = [
        Case(layout=layout, method=method, scenario=scenario)
        for layout in manifest.layouts
        for method in manifest.methods
        for seed in manifest.seeds
        for scenario in sample_scenarios(layout, method, seed, manifest.episodes_per_cell)
    ]
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "plan.json.tmp"
    temporary.write_text(json.dumps({
        "manifest": manifest.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json") for case in cases],
    }, indent=2) + "\n", encoding="utf-8")
    temporary.replace(plan_path)
    return manifest, cases


def _save_summary(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def run_campaign(manifest: Campaign, cases: list[Case], output: Path) -> bool:
    rows = []
    for index, case in enumerate(cases, 1):
        directory = output / f"case-{index:06d}"
        attempts = sorted(directory.glob("attempt-*"))
        attempt = attempts[-1] if attempts else directory / "attempt-000001"
        result_file = attempt / "result.json"
        result = None
        if result_file.is_file():
            result = EpisodeResult.model_validate_json(result_file.read_text(encoding="utf-8"))
        if result is None or result.classification != "behavior":
            if attempts:
                attempt = directory / f"attempt-{int(attempt.name.split('-')[-1]) + 1:06d}"
            result = run_gazebo(case.scenario, attempt, manifest.timeout, manifest.watchdog)
        metrics_file = attempt / "metrics.json"
        metrics = json.loads(metrics_file.read_text(encoding="utf-8")) if metrics_file.is_file() else {}
        rows.append({
            "case": index, "layout": case.layout, "method": case.method, "seed": case.scenario.seed,
            "scenario_hash": case.scenario.content_hash(), "status": result.status,
            "classification": result.classification, "sim_duration_s": result.sim_duration,
            "wall_duration_s": result.wall_duration, "path_length_m": metrics.get("path_length_m"),
            "position_rmse_m": metrics.get("position_rmse_m"),
            "goals_reached": metrics.get("goals_reached"),
            "bundle": attempt.relative_to(output).as_posix(),
        })
        _save_summary(output / "results.csv", rows)
        if result.classification != "behavior":
            return False
    return True
