"""Plan and resume sequential simulation campaigns from a YAML manifest."""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
from pathlib import Path
from queue import SimpleQueue
from threading import Event

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .benchmark import Layout, Sampling, sample_scenarios
from .campaign_index import refresh_index
from .gazebo import run_gazebo
from .results import EpisodeResult
from .scenario import Scenario
from .workers import WorkerLimits, remove_worker, run_worker


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
        rows.append(_summary_row(index, case, output, attempt, result))
        _save_summary(output / "results.csv", rows)
        if result.classification != "behavior":
            return False
    return True


def _summary_row(index: int, case: Case, output: Path, bundle: Path, result: EpisodeResult) -> dict:
    metrics_path = bundle / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.is_file() else {}
    return {
        "case": index, "layout": case.layout, "method": case.method, "seed": case.scenario.seed,
        "scenario_hash": case.scenario.content_hash(), "status": result.status,
        "classification": result.classification, "sim_duration_s": result.sim_duration,
        "wall_duration_s": result.wall_duration, "path_length_m": metrics.get("path_length_m"),
        "position_rmse_m": metrics.get("position_rmse_m"), "goals_reached": metrics.get("goals_reached"),
        "bundle": bundle.relative_to(output).as_posix(),
    }


def _execute_case(
    index: int, case: Case, manifest: Campaign, output: Path, limits: WorkerLimits, retries: int,
    domain_id: int, cancelled: Event,
) -> dict:
    directory = output / f"case-{index:06d}"
    failures = 0
    number = 0
    for attempt in sorted(directory.glob("attempt-*")):
        number = int(attempt.name.removeprefix("attempt-"))
        bundle = attempt if (attempt / "result.json").is_file() else attempt / "bundle"
        result_path = bundle / "result.json"
        if not result_path.is_file():
            remove_worker(bundle)
            delivered = attempt / "outputs" / bundle.name
            if not bundle.exists() and delivered.exists():
                delivered.replace(bundle)
        if not result_path.is_file():
            result = EpisodeResult(status="interrupted", classification="interrupted",
                                   reason="Previous scheduler stopped before the attempt completed")
            bundle.mkdir(exist_ok=True)
            result_path.write_text(result.model_dump_json(), encoding="utf-8")
        else:
            result = EpisodeResult.model_validate_json(result_path.read_text(encoding="utf-8"))
        if result.classification == "behavior":
            return _summary_row(index, case, output, bundle, result)
        failures += int(result.classification == "infrastructure")

    while failures <= retries:
        number += 1
        bundle = directory / f"attempt-{number:06d}" / "bundle"
        if cancelled.is_set():
            result = EpisodeResult(status="interrupted", classification="interrupted")
            break
        result = run_worker(case.scenario, bundle, manifest.timeout, manifest.watchdog, limits, domain_id=domain_id)
        if result.classification != "infrastructure":
            break
        failures += 1
    return _summary_row(index, case, output, bundle, result)


def run_parallel_campaign(
    manifest: Campaign, cases: list[Case], output: Path, workers: int,
    limits: WorkerLimits, retries: int = 2,
) -> bool:
    if not 1 <= workers <= 80 or retries < 0:
        raise ValueError("Worker count must be 1–80 and retries must be nonnegative")
    output = output.resolve()
    available = SimpleQueue()
    for domain_id in range(10, 10 + workers):
        available.put(domain_id)
    cancelled = Event()

    def execute(index, case):
        domain_id = available.get()
        try:
            return _execute_case(index, case, manifest, output, limits, retries, domain_id, cancelled)
        finally:
            available.put(domain_id)

    with (output / ".campaign.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("A scheduler is already running this campaign") from error
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(execute, index, case)
                for index, case in enumerate(cases, 1)
            ]
            rows = []
            try:
                for future in as_completed(futures):
                    rows.append(future.result())
                    _save_summary(output / "results.csv", sorted(rows, key=lambda row: row["case"]))
            except KeyboardInterrupt:
                cancelled.set()
                for future in futures:
                    future.cancel()
                for attempt in output.glob("case-*/attempt-*"):
                    bundle = attempt / "bundle"
                    if not (bundle / "result.json").exists():
                        remove_worker(bundle)
                raise
        refresh_index(output)
        return all(row["classification"] == "behavior" for row in rows)
