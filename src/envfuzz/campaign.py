"""Plan and resume sequential simulation campaigns from a YAML manifest."""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
import os
from pathlib import Path
from queue import SimpleQueue
from threading import Event
import time
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .benchmark import Layout, Sampling, sample_scenarios
from . import claims
from .campaign_index import refresh_index
from .claims import LEDGER_FILENAME, ClaimStore, verified_attempt
from .gazebo import run_gazebo
from .results import EpisodeResult
from .scenario import Scenario
from .workers import WorkerLimits, remove_worker, run_worker, worker_deadline

LEASE_SAFETY_MARGIN_S = 15.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


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


def _read_manifest(path: Path) -> Campaign:
    return Campaign.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def prepare_campaign(path: Path, output: Path) -> tuple[Campaign, list[Case]]:
    manifest = _read_manifest(path)
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


def load_plan(output: Path) -> tuple[Campaign, list[Case]]:
    """Read the frozen plan from a campaign directory without a manifest file."""
    plan_path = output / "plan.json"
    if not plan_path.is_file():
        raise ValueError(f"No frozen plan.json in {output}. Run campaign-approve first.")
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    return Campaign.model_validate(saved["manifest"]), [Case.model_validate(row) for row in saved["cases"]]


def join_campaign(path: Path, output: Path) -> tuple[Campaign, list[Case]]:
    """Load the frozen plan for a worker, checking its manifest, without writing any file."""
    saved, cases = load_plan(output)
    if _read_manifest(path).model_dump(mode="json") != saved.model_dump(mode="json"):
        raise ValueError("This output directory contains a different campaign manifest")
    return saved, cases


def reject_durable_ledger(output: Path) -> None:
    """Refuse single-host commands in a directory that durable workers manage."""
    if (output / LEDGER_FILENAME).exists():
        raise ValueError("This campaign directory is managed by durable workers. "
                         "Continue it with campaign-worker and campaign-reconcile.")


def _save_summary(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def run_campaign(manifest: Campaign, cases: list[Case], output: Path) -> bool:
    reject_durable_ledger(output)
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


def _summary_row(index: int, case: Case, output: Path, bundle: Path | None, result: EpisodeResult) -> dict:
    metrics_path = bundle / "metrics.json" if bundle is not None else None
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path and metrics_path.is_file() else {}
    return {
        "case": index, "layout": case.layout, "method": case.method, "seed": case.scenario.seed,
        "scenario_hash": case.scenario.content_hash(), "status": result.status,
        "classification": result.classification, "sim_duration_s": result.sim_duration,
        "wall_duration_s": result.wall_duration, "path_length_m": metrics.get("path_length_m"),
        "position_rmse_m": metrics.get("position_rmse_m"), "goals_reached": metrics.get("goals_reached"),
        "bundle": bundle.relative_to(output).as_posix() if bundle is not None else "",
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
    reject_durable_ledger(output)
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


class WorkerHalted(Exception):
    """A durable worker stopped because its own attempts kept failing."""

    def __init__(self, message: str, committed: int) -> None:
        super().__init__(message)
        self.committed = committed


def minimum_lease(watchdog: float) -> float:
    """Shortest lease a worker may request: the container deadline plus two ledger lock waits."""
    return worker_deadline(watchdog) + 2 * claims.BUSY_TIMEOUT_S + LEASE_SAFETY_MARGIN_S


def _validate_worker_settings(manifest: Campaign, lease_s: float, poll_s: float,
                              max_consecutive_failures: int) -> None:
    floor = minimum_lease(manifest.watchdog)
    if lease_s <= floor:
        raise ValueError(f"The lease must exceed {floor:g} s: the worker deadline plus ledger lock waits")
    if poll_s <= 0:
        raise ValueError("The poll interval must be positive")
    if max_consecutive_failures < 1:
        raise ValueError("The consecutive failure limit must be at least 1")


def run_durable_worker(
    manifest: Campaign, cases: list[Case], output: Path, worker_id: str, limits: WorkerLimits,
    *, lease_s: float = 600, retries: int | None = None, wait: bool = True, poll_s: float = 10.0,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    store: ClaimStore | None = None, sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Claim and execute jobs from a shared campaign root until none can still finish here.

    With ``wait`` the worker polls while other workers hold live leases, so it can retry
    a job whose owner disappears. Returns the number of authoritative results committed.
    Raises WorkerHalted after ``max_consecutive_failures`` infrastructure failures in a row,
    or when every attempt this worker ran was an infrastructure failure.
    """
    output = output.resolve()
    _validate_worker_settings(manifest, lease_s, poll_s, max_consecutive_failures)
    store = store or ClaimStore(output)
    if store.root.resolve() != output:
        raise ValueError("The claim store must belong to the campaign directory")
    store.join(len(cases), retries=retries)
    store.require_approved(worker_id)
    committed = attempts = consecutive = 0
    last_failure: EpisodeResult | None = None
    while True:
        claim = store.claim(worker_id, lease_s, retries)
        if claim is None:
            if not wait or store.unfinished_count(retries) == 0:
                break
            sleep(poll_s)
            continue
        bundle = output / f"case-{claim.case_id:06d}" / f"attempt-{claim.attempt:06d}" / "bundle"
        result, from_bundle = _run_claimed_episode(cases[claim.case_id - 1], bundle, manifest, limits)
        committed += int(store.complete(claim, worker_id, result, bundle, retries, from_bundle=from_bundle))
        attempts += 1
        if result.classification == "interrupted":
            return committed
        if result.classification != "infrastructure":
            consecutive = 0
            continue
        consecutive, last_failure = consecutive + 1, result
        if consecutive >= max_consecutive_failures:
            raise WorkerHalted(f"Worker {worker_id} stopped after {consecutive} consecutive infrastructure "
                               f"failures (last: {_describe(last_failure)}). {committed} authoritative "
                               "results committed. Check Docker and the image on this host.", committed)
    if attempts and consecutive == attempts:
        raise WorkerHalted(f"Worker {worker_id} recorded only infrastructure failures ({attempts} attempts; "
                           f"last: {_describe(last_failure)}). {committed} authoritative results committed.",
                           committed)
    return committed


def _describe(result: EpisodeResult | None) -> str:
    if result is None:
        return "none"
    return f"{result.status}: {result.reason}" if result.reason else result.status


def _run_claimed_episode(
    case: Case, bundle: Path, manifest: Campaign, limits: WorkerLimits,
) -> tuple[EpisodeResult, bool]:
    """Run one claimed attempt; the flag says whether the result came from its own bundle."""
    try:
        result = run_worker(case.scenario, bundle, manifest.timeout, manifest.watchdog, limits)
    except (OSError, ValueError) as error:
        return EpisodeResult(status="launch_failed", classification="infrastructure", reason=str(error)), False
    if not (bundle / "result.json").is_file():
        return EpisodeResult(status="simulator_failed", classification="infrastructure",
                             reason="The worker returned without writing result.json"), False
    return result, True


def reconcile_durable_campaign(output: Path) -> tuple[bool, dict]:
    """Write the authoritative summary and the attempt audit for a durable campaign.

    The ledger is checked before anything is written. results.csv and claims.json are
    replaced atomically, and only after every reported bundle and the index were verified.
    Returns whether every case has an authoritative result, and the ledger diagnostics
    that are also saved as ``claims.json``.
    """
    output = output.resolve()
    _, cases = load_plan(output)
    store = ClaimStore(output)
    store.join(len(cases))
    report = store.diagnostics()
    rows = [row for job in report["jobs"] if (row := _reconciled_row(output, cases, job)) is not None]
    refresh_index(output)
    if rows:
        _save_summary(output / "results.csv", rows)
    temporary = output / "claims.json.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output / "claims.json")
    return report["counts"]["completed"] == len(cases), report


def _reconciled_row(output: Path, cases: list[Case], job: dict) -> dict | None:
    """Summarize the authoritative attempt, or the latest finished one when none exists."""
    finished = [attempt for attempt in job["attempts"] if attempt["finished_at"] is not None]
    chosen = next((attempt for attempt in finished if attempt["attempt"] == job["authoritative_attempt"]),
                  finished[-1] if finished else None)
    if chosen is None:
        return None
    bundle, result = verified_attempt(output, chosen)
    return _summary_row(job["case_id"], cases[job["case_id"] - 1], output, bundle, result)
