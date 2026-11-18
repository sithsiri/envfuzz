"""Durable claim ledger: leases, bounded retries, approval, and authoritative deduplication."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import closing
import hashlib
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from envfuzz.claims import (
    LEDGER_SCHEMA_VERSION, MAX_LEASE_S, ClaimError, ClaimStore, JobClaim, plan_digest, verified_attempt,
)
from envfuzz.results import EpisodeResult

BEHAVIOR = EpisodeResult(status="completed", classification="behavior", score=3)
INFRASTRUCTURE = EpisodeResult(status="watchdog", classification="infrastructure", reason="stuck")


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_store(root: Path, cases: int = 3, retries: int = 1, approved=("host-a", "host-b", "host-c"),
               clock: FakeClock | None = None, host: str = "node-1") -> ClaimStore:
    if not (root / "plan.json").exists():
        (root / "plan.json").write_text('{"cases": "fixture"}\n', encoding="utf-8")
    store = ClaimStore(root, clock=clock or FakeClock(), host=host)
    store.initialize(cases, retries=retries)
    if approved:
        store.approve(approved)
    return store


def bundle_for(root: Path, claim: JobClaim, result: EpisodeResult | None = None) -> Path:
    bundle = root / f"case-{claim.case_id:06d}" / f"attempt-{claim.attempt:06d}" / "bundle"
    bundle.mkdir(parents=True)
    if result is not None:
        (bundle / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    return bundle


def job(report: dict, case_id: int) -> dict:
    return next(row for row in report["jobs"] if row["case_id"] == case_id)


def test_claims_follow_case_order_and_record_versioned_manifest(tmp_path):
    clock = FakeClock()
    store = make_store(tmp_path, cases=3, clock=clock)

    claims = [store.claim("host-a", lease_s=60) for _ in range(3)]

    assert [(claim.case_id, claim.attempt) for claim in claims] == [(1, 1), (2, 1), (3, 1)]
    assert {claim.plan_sha256 for claim in claims} == {plan_digest(tmp_path)}
    assert claims[0].worker_id == "host-a"
    assert claims[0].lease_expires == clock.now + 60
    assert store.claim("host-a", lease_s=60) is None
    report = store.diagnostics()
    assert report["ledger_version"] == LEDGER_SCHEMA_VERSION
    assert report["plan_sha256"] == hashlib.sha256((tmp_path / "plan.json").read_bytes()).hexdigest()
    assert report["retries"] == 1 and report["max_attempts"] == 2
    assert report["counts"] == {"queued": 0, "claimed": 3, "completed": 0, "exhausted": 0}


def test_unapproved_and_malformed_workers_cannot_claim(tmp_path):
    store = make_store(tmp_path, approved=("host-a",))

    with pytest.raises(ClaimError, match="not approved"):
        store.claim("intruder", lease_s=60)
    with pytest.raises(ClaimError, match="not approved"):
        store.require_approved("intruder")
    for bad in ("", "../etc", "a b", "x" * 65):
        with pytest.raises(ClaimError, match="Worker IDs"):
            store.claim(bad, lease_s=60)
        with pytest.raises(ClaimError, match="Worker IDs"):
            store.approve([bad])
    store.require_approved("host-a")
    assert store.approved_workers() == ["host-a"]


def test_worker_path_requires_an_allowlist(tmp_path):
    store = make_store(tmp_path, approved=())

    with pytest.raises(ClaimError, match="No workers are approved"):
        store.require_approved("host-a")


def test_lease_must_be_positive_finite_and_bounded(tmp_path):
    store = make_store(tmp_path)

    for lease in (0, -1, float("inf"), float("nan"), MAX_LEASE_S + 1):
        with pytest.raises(ValueError, match="Lease"):
            store.claim("host-a", lease_s=lease)


def test_expired_lease_is_retried_by_another_worker_and_stays_visible(tmp_path):
    clock = FakeClock(100.0)
    store = make_store(tmp_path, cases=1, retries=1, clock=clock)
    lost = store.claim("host-a", lease_s=10)
    clock.now = 105.0
    assert store.claim("host-b", lease_s=10) is None

    clock.now = 110.0
    recovered = store.claim("host-b", lease_s=10)

    assert (lost.attempt, recovered.attempt) == (1, 2)
    attempts = job(store.diagnostics(), 1)["attempts"]
    assert [(row["attempt"], row["worker_id"], row["outcome"]) for row in attempts] == [
        (1, "host-a", "lease_expired"), (2, "host-b", "running"),
    ]


def test_expired_leases_exhaust_the_budget_instead_of_sticking(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=1, clock=clock)
    store.claim("host-a", lease_s=5)
    clock.now = 10.0
    store.claim("host-b", lease_s=5)
    clock.now = 20.0

    assert store.claim("host-c", lease_s=5) is None

    report = store.diagnostics()
    assert job(report, 1)["state"] == "exhausted"
    assert job(report, 1)["authoritative_attempt"] is None
    assert [row["outcome"] for row in job(report, 1)["attempts"]] == ["lease_expired", "lease_expired"]
    assert store.unfinished_count() == 0
    assert store.complete_count() == 0


def test_unfinished_count_waits_for_live_leases(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=2, retries=0, clock=clock)
    store.claim("host-a", lease_s=5)

    assert store.unfinished_count() == 2
    clock.now = 6.0
    assert store.unfinished_count() == 1
    store.claim("host-b", lease_s=5)
    clock.now = 12.0
    assert store.unfinished_count() == 0
    assert store.claim("host-b", lease_s=5) is None
    assert store.diagnostics()["counts"]["exhausted"] == 2


def test_infrastructure_failures_retry_within_budget_then_exhaust(tmp_path):
    store = make_store(tmp_path, cases=1, retries=1)
    first = store.claim("host-a", lease_s=60)
    assert not store.complete(first, "host-a", INFRASTRUCTURE, bundle_for(tmp_path, first, INFRASTRUCTURE))
    second = store.claim("host-b", lease_s=60)

    assert not store.complete(second, "host-b", INFRASTRUCTURE, bundle_for(tmp_path, second, INFRASTRUCTURE))

    assert store.claim("host-c", lease_s=60) is None
    report = job(store.diagnostics(), 1)
    assert report["state"] == "exhausted"
    assert report["authoritative_attempt"] is None
    assert [row["outcome"] for row in report["attempts"]] == ["failed", "failed"]
    assert report["attempts"][0]["reason"] == "stuck"


def test_late_result_after_recovery_is_recorded_but_never_authoritative(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=2, clock=clock)
    lost = store.claim("host-a", lease_s=5)
    clock.now = 10.0
    recovered = store.claim("host-b", lease_s=5)
    assert store.complete(recovered, "host-b", BEHAVIOR, bundle_for(tmp_path, recovered, BEHAVIOR))

    clock.now = 11.0
    late = EpisodeResult(status="mission_failed", classification="behavior", score=9)
    assert not store.complete(lost, "host-a", late, bundle_for(tmp_path, lost, late))

    report = job(store.diagnostics(), 1)
    assert report["state"] == "completed"
    assert report["authoritative_attempt"] == 2
    assert [(row["attempt"], row["outcome"], row["status"]) for row in report["attempts"]] == [
        (1, "duplicate", "mission_failed"), (2, "authoritative", "completed"),
    ]
    assert store.complete_count() == 1
    assert store.claim("host-c", lease_s=5) is None


def test_late_failure_does_not_requeue_a_job_held_by_a_newer_attempt(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=3, clock=clock)
    lost = store.claim("host-a", lease_s=5)
    clock.now = 10.0
    store.claim("host-b", lease_s=60)

    assert not store.complete(lost, "host-a", INFRASTRUCTURE, bundle_for(tmp_path, lost, INFRASTRUCTURE))

    assert store.claim("host-c", lease_s=60) is None
    report = job(store.diagnostics(), 1)
    assert report["state"] == "claimed" and report["lease_owner"] == "host-b"


def test_first_committed_behavior_result_wins_even_from_an_expired_attempt(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=2, clock=clock)
    slow = store.claim("host-a", lease_s=5)
    clock.now = 10.0
    retry = store.claim("host-b", lease_s=60)

    assert store.complete(slow, "host-a", BEHAVIOR, bundle_for(tmp_path, slow, BEHAVIOR))
    assert not store.complete(retry, "host-b", BEHAVIOR, bundle_for(tmp_path, retry, BEHAVIOR))

    report = job(store.diagnostics(), 1)
    assert report["authoritative_attempt"] == 1
    assert report["lease_owner"] is None
    assert [row["outcome"] for row in report["attempts"]] == ["authoritative", "duplicate"]


def test_late_behavior_result_completes_an_exhausted_job(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=0, clock=clock)
    slow = store.claim("host-a", lease_s=5)
    clock.now = 10.0
    assert store.claim("host-b", lease_s=5) is None
    assert job(store.diagnostics(), 1)["state"] == "exhausted"

    assert store.complete(slow, "host-a", BEHAVIOR, bundle_for(tmp_path, slow, BEHAVIOR))

    assert job(store.diagnostics(), 1)["state"] == "completed"
    assert store.complete_count() == 1


def test_attempt_records_are_append_only(tmp_path):
    store = make_store(tmp_path, cases=1)
    claim = store.claim("host-a", lease_s=60)
    bundle = bundle_for(tmp_path, claim, BEHAVIOR)
    assert store.complete(claim, "host-a", BEHAVIOR, bundle)

    with pytest.raises(ClaimError, match="already recorded"):
        store.complete(claim, "host-a", BEHAVIOR, bundle)
    with sqlite3.connect(store.path) as database:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            database.execute("UPDATE job_attempts SET status='watchdog' WHERE case_id=1")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            database.execute("DELETE FROM job_attempts")
        with pytest.raises(sqlite3.DatabaseError, match="authoritative"):
            database.execute("UPDATE jobs SET authoritative_attempt=NULL WHERE case_id=1")
    assert job(store.diagnostics(), 1)["attempts"][0]["status"] == "completed"


def test_unfinished_attempt_identity_cannot_be_rewritten(tmp_path):
    store = make_store(tmp_path, cases=1)
    store.claim("host-a", lease_s=60)

    with sqlite3.connect(store.path) as database:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            database.execute("UPDATE job_attempts SET worker_id='host-b'")


def test_attempt_provenance_is_preserved_for_audit(tmp_path):
    clock = FakeClock(50.0)
    store = make_store(tmp_path, cases=1, clock=clock, host="lab-node-7")
    claim = store.claim("host-a", lease_s=30)
    bundle = bundle_for(tmp_path, claim, BEHAVIOR)
    clock.now = 70.0

    store.complete(claim, "host-a", BEHAVIOR, bundle)

    [attempt] = job(store.diagnostics(), 1)["attempts"]
    assert attempt == {
        "attempt": 1, "worker_id": "host-a", "host": "lab-node-7", "claimed_at": 50.0,
        "lease_expires": 80.0, "finished_at": 70.0, "status": "completed",
        "classification": "behavior", "score": 3, "reason": None,
        "bundle": "case-000001/attempt-000001/bundle",
        "result_sha256": hashlib.sha256((bundle / "result.json").read_bytes()).hexdigest(),
        "metrics_sha256": None, "outcome": "authoritative", "clock_skew_suspected": False,
    }


def test_completion_rejects_wrong_worker_unknown_attempt_and_foreign_bundles(tmp_path):
    store = make_store(tmp_path, cases=2)
    claim = store.claim("host-a", lease_s=60)
    bundle = bundle_for(tmp_path, claim, BEHAVIOR)

    with pytest.raises(ClaimError, match="belongs to host-a"):
        store.complete(claim, "host-b", BEHAVIOR, bundle)
    with pytest.raises(ClaimError, match="Unknown"):
        store.complete(JobClaim(2, 7), "host-a", INFRASTRUCTURE, tmp_path / "case-000002", from_bundle=False)
    other_case = tmp_path / "case-000002" / "attempt-000001" / "bundle"
    with pytest.raises(ClaimError, match="inside"):
        store.complete(claim, "host-a", BEHAVIOR, other_case)
    with pytest.raises(ClaimError, match="inside"):
        store.complete(claim, "host-a", BEHAVIOR, tmp_path.parent / "elsewhere")


def test_changed_plan_or_case_count_is_rejected(tmp_path):
    make_store(tmp_path, cases=2)

    with pytest.raises(ClaimError, match="case count"):
        ClaimStore(tmp_path).initialize(3, retries=1)
    (tmp_path / "plan.json").write_text('{"cases": "edited"}\n', encoding="utf-8")
    with pytest.raises(ClaimError, match="different campaign plan"):
        ClaimStore(tmp_path).initialize(2, retries=1)


def test_store_with_a_stale_plan_digest_cannot_claim(tmp_path):
    stale = make_store(tmp_path, cases=1)
    (tmp_path / "plan.json").write_text('{"cases": "edited"}\n', encoding="utf-8")

    with pytest.raises(ClaimError, match="different campaign plan"):
        stale.claim("host-a", lease_s=60)


def test_claim_requires_an_initialized_ledger(tmp_path):
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ClaimError, match="No claim ledger"):
        ClaimStore(tmp_path).claim("host-a", lease_s=60, retries=1)
    with pytest.raises(ClaimError, match="No claim ledger"):
        ClaimStore(tmp_path).diagnostics()


def test_retry_budget_is_recorded_once_and_enforced(tmp_path):
    store = make_store(tmp_path, cases=1, retries=2)

    with pytest.raises(ClaimError, match="retry budget"):
        store.claim("host-a", lease_s=60, retries=1)
    with pytest.raises(ClaimError, match="retry budget"):
        ClaimStore(tmp_path).initialize(1, retries=3)
    with pytest.raises(ValueError, match="nonnegative"):
        ClaimStore(tmp_path).initialize(1, retries=-1)
    claim = store.claim("host-a", lease_s=60, retries=2)
    assert claim.attempt == 1


def test_budget_must_be_known_before_claiming(tmp_path):
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    store = ClaimStore(tmp_path)
    store.initialize(1)

    with pytest.raises(ClaimError, match="retry budget"):
        store.claim("host-a", lease_s=60)
    with pytest.raises(ValueError, match="nonnegative"):
        store.claim("host-a", lease_s=60, retries=-1)
    store.initialize(1, retries=0)
    assert store.claim("host-a", lease_s=60).attempt == 1


def test_ledgers_from_an_unversioned_schema_are_rejected(tmp_path):
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    with sqlite3.connect(tmp_path / "claims.sqlite3") as database:
        database.execute("CREATE TABLE jobs (case_id INTEGER PRIMARY KEY)")

    with pytest.raises(ClaimError, match="unsupported"):
        ClaimStore(tmp_path).initialize(1, retries=1)


def test_ledger_version_mismatch_is_rejected(tmp_path):
    make_store(tmp_path, cases=1)
    with sqlite3.connect(tmp_path / "claims.sqlite3") as database:
        database.execute("DROP TRIGGER ledger_meta_no_update")
        database.execute("UPDATE ledger_meta SET value='999' WHERE key='ledger_version'")

    with pytest.raises(ClaimError, match="version 999"):
        ClaimStore(tmp_path).initialize(1, retries=1)


def test_ledger_does_not_use_wal_on_shared_filesystems(tmp_path):
    store = make_store(tmp_path, cases=1)

    with sqlite3.connect(store.path) as database:
        assert database.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def _claim_all(root: str, worker: str) -> list[tuple[int, int]]:
    store = ClaimStore(Path(root), host=worker)
    claimed = []
    while (claim := store.claim(worker, lease_s=600)) is not None:
        claimed.append((claim.case_id, claim.attempt))
    return claimed


def test_concurrent_thread_claimers_never_double_claim(tmp_path):
    workers = [f"thread-{index}" for index in range(8)]
    make_store(tmp_path, cases=40, approved=workers)

    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        batches = list(pool.map(_claim_all, [str(tmp_path)] * len(workers), workers))

    claimed = [item for batch in batches for item in batch]
    assert sorted(claimed) == [(case, 1) for case in range(1, 41)]


def test_concurrent_process_claimers_never_double_claim(tmp_path):
    workers = [f"proc-{index}" for index in range(4)]
    make_store(tmp_path, cases=24, approved=workers)

    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=len(workers), mp_context=context) as pool:
        batches = list(pool.map(_claim_all, [str(tmp_path)] * len(workers), workers))

    claimed = [item for batch in batches for item in batch]
    assert sorted(claimed) == [(case, 1) for case in range(1, 25)]


def test_authoritative_results_report_every_case_read_only(tmp_path):
    store = make_store(tmp_path, cases=2)
    claim = store.claim("host-a", lease_s=60)
    bundle = bundle_for(tmp_path, claim, BEHAVIOR)
    store.complete(claim, "host-a", BEHAVIOR, bundle)
    before = store.path.read_bytes()

    records = ClaimStore(tmp_path).authoritative_results()

    assert records[2] is None
    assert {key: records[1][key] for key in ("attempt", "status", "classification", "bundle")} == {
        "attempt": 1, "status": "completed", "classification": "behavior",
        "bundle": "case-000001/attempt-000001/bundle",
    }
    assert records[1]["result_sha256"] == hashlib.sha256((bundle / "result.json").read_bytes()).hexdigest()
    assert store.path.read_bytes() == before
    with pytest.raises(ClaimError, match="No claim ledger"):
        ClaimStore(tmp_path / "missing").authoritative_results()


def test_new_ledger_is_refused_where_single_host_results_exist(tmp_path):
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    (tmp_path / "case-000001" / "attempt-000001").mkdir(parents=True)

    with pytest.raises(ClaimError, match="single-host"):
        ClaimStore(tmp_path).initialize(1, retries=0)
    assert not (tmp_path / "claims.sqlite3").exists()
    (tmp_path / "case-000001" / "attempt-000001").rmdir()
    (tmp_path / "case-000001").rmdir()
    (tmp_path / "results.csv").write_text("case\n", encoding="utf-8")
    with pytest.raises(ClaimError, match="single-host"):
        ClaimStore(tmp_path).initialize(1, retries=0)


def test_existing_ledger_can_be_rejoined_after_attempt_folders_exist(tmp_path):
    store = make_store(tmp_path, cases=1)
    claim = store.claim("host-a", lease_s=60)
    bundle_for(tmp_path, claim, BEHAVIOR)

    ClaimStore(tmp_path).initialize(1, retries=1)
    ClaimStore(tmp_path).join(1)


def test_result_not_produced_by_the_attempt_records_no_bundle_or_hash(tmp_path):
    store = make_store(tmp_path, cases=1)
    claim = store.claim("host-a", lease_s=60)
    stale = bundle_for(tmp_path, claim, BEHAVIOR)
    failure = EpisodeResult(status="launch_failed", classification="infrastructure", reason="exists")

    store.complete(claim, "host-a", failure, stale, from_bundle=False)

    [attempt] = job(store.diagnostics(), 1)["attempts"]
    assert (attempt["bundle"], attempt["result_sha256"], attempt["metrics_sha256"]) == (None, None, None)


def test_completion_records_the_metrics_hash(tmp_path):
    store = make_store(tmp_path, cases=1)
    claim = store.claim("host-a", lease_s=60)
    bundle = bundle_for(tmp_path, claim, BEHAVIOR)
    (bundle / "metrics.json").write_text('{"path_length_m": 1.0}', encoding="utf-8")

    store.complete(claim, "host-a", BEHAVIOR, bundle)

    [attempt] = job(store.diagnostics(), 1)["attempts"]
    assert attempt["metrics_sha256"] == hashlib.sha256((bundle / "metrics.json").read_bytes()).hexdigest()


def test_join_checks_the_ledger_without_writing(tmp_path):
    store = make_store(tmp_path, cases=2, retries=1)
    before = store.path.read_bytes()

    store.join(2)
    store.join(2, retries=1)
    with pytest.raises(ClaimError, match="retry budget"):
        store.join(2, retries=3)
    with pytest.raises(ClaimError, match="case count"):
        store.join(5)
    assert store.path.read_bytes() == before
    with pytest.raises(ClaimError, match="No claim ledger"):
        ClaimStore(tmp_path / "absent").join(2)
    unbudgeted = tmp_path / "unbudgeted"
    unbudgeted.mkdir()
    (unbudgeted / "plan.json").write_text("{}", encoding="utf-8")
    ClaimStore(unbudgeted).initialize(1)
    with pytest.raises(ClaimError, match="retry budget"):
        ClaimStore(unbudgeted).join(1)


def test_live_attempt_stays_running_after_another_attempt_completes_the_job(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=2, clock=clock)
    slow = store.claim("host-a", lease_s=5)
    clock.now = 10.0
    store.claim("host-b", lease_s=60)

    store.complete(slow, "host-a", BEHAVIOR, bundle_for(tmp_path, slow, BEHAVIOR))

    assert [row["outcome"] for row in job(store.diagnostics(), 1)["attempts"]] == ["authoritative", "running"]
    clock.now = 80.0
    assert [row["outcome"] for row in job(store.diagnostics(), 1)["attempts"]] == [
        "authoritative", "lease_expired"]


def test_read_only_access_recovers_a_hot_journal_from_a_killed_writer(tmp_path):
    store = make_store(tmp_path, cases=2)
    child = (
        "import os, sqlite3, sys\n"
        "d = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
        "d.execute('PRAGMA cache_size=1')\n"
        "d.execute('BEGIN IMMEDIATE')\n"
        "d.execute('CREATE TABLE pad(x)')\n"
        "d.executemany('INSERT INTO pad VALUES (?)', [('x' * 2000,)] * 500)\n"
        "d.execute('UPDATE jobs SET attempts = attempts + 7')\n"
        "os._exit(9)\n"
    )
    subprocess.run([sys.executable, "-c", child, str(store.path)], check=False)
    assert (tmp_path / "claims.sqlite3-journal").exists()

    records = ClaimStore(tmp_path).authoritative_results()

    assert records == {1: None, 2: None}
    assert job(store.diagnostics(), 1)["attempt_count"] == 0


def test_sqlite_failures_become_actionable_claim_errors(tmp_path, monkeypatch):
    store = make_store(tmp_path, cases=1)
    monkeypatch.setattr("envfuzz.claims.BUSY_TIMEOUT_S", 0.05)
    holder = sqlite3.connect(store.path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(ClaimError, match="locked"):
            store.claim("host-a", lease_s=60)
        with pytest.raises(ClaimError, match="locked"):
            store.authoritative_results()
        with pytest.raises(ClaimError, match="locked"):
            ClaimStore(tmp_path).initialize(1, retries=1)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    (tmp_path / "corrupt").mkdir()
    (tmp_path / "corrupt" / "claims.sqlite3").write_bytes(b"not a database" * 100)
    with pytest.raises(ClaimError, match="unavailable"):
        ClaimStore(tmp_path / "corrupt").diagnostics()


def test_unopenable_ledger_and_escaping_bundle_paths_are_refused(tmp_path):
    store = make_store(tmp_path, cases=1)
    store.path.chmod(0)
    try:
        with pytest.raises(ClaimError, match="unavailable"):
            store.claim("host-a", lease_s=60)
    finally:
        store.path.chmod(0o644)
    record = {"bundle": "../elsewhere/bundle", "result_sha256": "0" * 64, "metrics_sha256": None,
              "status": "completed", "classification": "behavior", "score": 0, "reason": None}
    with pytest.raises(ClaimError, match="outside the campaign"):
        verified_attempt(tmp_path, record)


# Adversarial review (da-ledger): launch failures, lock waits, symlinks, schema guards.

LAUNCH = EpisodeResult(status="launch_failed", classification="infrastructure", reason="no image")


def test_launch_failure_of_the_current_attempt_does_not_consume_budget(tmp_path):
    store = make_store(tmp_path, cases=1, retries=0)
    for number in (1, 2, 3):
        claim = store.claim("host-a", lease_s=60)
        assert claim.attempt == number
        store.complete(claim, "host-a", LAUNCH, tmp_path / "unused", from_bundle=False)
    report = job(store.diagnostics(), 1)
    assert (report["state"], report["attempt_count"], report["budget_used"]) == ("queued", 3, 0)
    started = store.claim("host-a", lease_s=60)

    store.complete(started, "host-a", INFRASTRUCTURE, bundle_for(tmp_path, started, INFRASTRUCTURE))

    report = job(store.diagnostics(), 1)
    assert (report["state"], report["budget_used"]) == ("exhausted", 1)
    assert store.claim("host-a", lease_s=60) is None


def test_launch_failure_reported_after_lease_expiry_keeps_the_expiry_charge(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=1, clock=clock)
    lost = store.claim("host-a", lease_s=5)
    clock.now = 10.0
    store.claim("host-b", lease_s=60)

    store.complete(lost, "host-a", LAUNCH, tmp_path / "unused", from_bundle=False)

    report = job(store.diagnostics(), 1)
    assert (report["state"], report["budget_used"], report["lease_owner"]) == ("claimed", 2, "host-b")


def test_behavior_results_must_come_from_a_consistent_bundle(tmp_path):
    store = make_store(tmp_path, cases=1)
    claim = store.claim("host-a", lease_s=60)
    bundle = bundle_for(tmp_path, claim)

    with pytest.raises(ClaimError, match="own bundle"):
        store.complete(claim, "host-a", BEHAVIOR, bundle, from_bundle=False)
    with pytest.raises(ClaimError, match="no result.json"):
        store.complete(claim, "host-a", BEHAVIOR, bundle)
    (bundle / "result.json").write_text(INFRASTRUCTURE.model_dump_json(), encoding="utf-8")
    with pytest.raises(ClaimError, match="disagrees"):
        store.complete(claim, "host-a", BEHAVIOR, bundle)
    assert job(store.diagnostics(), 1)["attempts"][0]["finished_at"] is None


def test_verified_attempt_requires_hashes_for_a_recorded_bundle(tmp_path):
    record = {"bundle": "case-000001/attempt-000001/bundle", "result_sha256": None, "metrics_sha256": None,
              "status": "completed", "classification": "behavior", "score": 0, "reason": None}

    with pytest.raises(ClaimError, match="without a result.json hash"):
        verified_attempt(tmp_path, record)
    bundle, result = verified_attempt(tmp_path, {**record, "bundle": None, "status": "launch_failed",
                                                 "classification": "infrastructure"})
    assert bundle is None and result.status == "launch_failed"


def _hold_write_lock(path: Path, seconds: float, ready) -> None:
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    ready.set()
    time.sleep(seconds)
    holder.execute("COMMIT")
    holder.close()


def test_lease_and_finish_times_are_read_after_the_write_lock(tmp_path):
    make_store(tmp_path, cases=1)
    store = ClaimStore(tmp_path)
    for action in ("claim", "complete"):
        ready = threading.Event()
        holder = threading.Thread(target=_hold_write_lock, args=(store.path, 0.4, ready))
        holder.start()
        ready.wait()
        started = time.time()
        if action == "claim":
            claim = store.claim("host-a", lease_s=5)
            assert claim.lease_expires >= started + 0.35 + 5
        else:
            store.complete(claim, "host-a", BEHAVIOR, bundle_for(tmp_path, claim, BEHAVIOR))
            assert job(store.diagnostics(), 1)["attempts"][0]["finished_at"] >= started + 0.35
        holder.join()


def test_far_future_lease_from_a_skewed_clock_is_treated_as_expired(tmp_path):
    honest = FakeClock(1_000.0)
    make_store(tmp_path, cases=1, retries=1, clock=honest)
    ClaimStore(tmp_path, clock=FakeClock(1_000.0 + 3.15e8)).claim("host-a", lease_s=60)
    store = ClaimStore(tmp_path, clock=honest)

    assert job(store.diagnostics(), 1)["attempts"][0]["outcome"] == "lease_expired"
    assert job(store.diagnostics(), 1)["attempts"][0]["clock_skew_suspected"] is True
    assert store.claim("host-b", lease_s=60).attempt == 2


def test_symlinked_ledger_or_plan_is_refused(tmp_path):
    store = make_store(tmp_path, cases=1)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    store.path.rename(elsewhere / "claims.sqlite3")
    store.path.symlink_to(elsewhere / "claims.sqlite3")

    for action in (lambda: store.join(1), store.diagnostics, store.authoritative_results,
                   lambda: store.claim("host-a", lease_s=60), lambda: store.initialize(1, retries=1)):
        with pytest.raises(ClaimError, match="symlink"):
            action()
    store.path.unlink()
    (elsewhere / "claims.sqlite3").rename(store.path)
    (tmp_path / "plan.json").rename(elsewhere / "plan.json")
    (tmp_path / "plan.json").symlink_to(elsewhere / "plan.json")
    with pytest.raises(ClaimError, match="symlink"):
        store.join(1)


def test_unversioned_wal_ledger_is_reported_clearly_and_left_unchanged(tmp_path):
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    path = tmp_path / "claims.sqlite3"
    with closing(sqlite3.connect(path)) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE jobs (case_id INTEGER PRIMARY KEY)")
        database.commit()
    before = path.read_bytes()
    store = ClaimStore(tmp_path)

    for action in (lambda: store.join(1), store.diagnostics, store.authoritative_results,
                   lambda: store.claim("host-a", lease_s=60, retries=1), lambda: store.initialize(1, retries=1)):
        with pytest.raises(ClaimError, match="unversioned"):
            action()
    assert path.read_bytes() == before


def test_rollback_journal_ledger_without_metadata_is_reported_as_unversioned(tmp_path):
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    with closing(sqlite3.connect(tmp_path / "claims.sqlite3")) as database:
        database.execute("CREATE TABLE unrelated (x)")
        database.commit()

    with pytest.raises(ClaimError, match="unversioned"):
        ClaimStore(tmp_path).diagnostics()
    with pytest.raises(ClaimError, match="unversioned"):
        ClaimStore(tmp_path).join(1)


def test_insert_or_replace_cannot_rewrite_attempts_jobs_or_metadata(tmp_path):
    store = make_store(tmp_path, cases=1)
    claim = store.claim("host-a", lease_s=60)
    store.complete(claim, "host-a", BEHAVIOR, bundle_for(tmp_path, claim, BEHAVIOR))
    before = store.diagnostics()

    with closing(sqlite3.connect(store.path, isolation_level=None)) as database:
        for statement in (
            "INSERT OR REPLACE INTO job_attempts(case_id, attempt, worker_id, host, claimed_at, lease_expires,"
            " finished_at, status, classification) VALUES (1, 1, 'host-a', 'h', 0, 0, 1, 'timeout', 'infrastructure')",
            "INSERT OR REPLACE INTO jobs(case_id, plan_sha256, state, attempts) "
            "SELECT case_id, plan_sha256, 'queued', 0 FROM jobs",
            "DELETE FROM jobs",
            "UPDATE jobs SET attempts = 0",
            "UPDATE jobs SET plan_sha256 = 'x'",
            "INSERT OR REPLACE INTO ledger_meta VALUES ('retries', '9')",
            "UPDATE ledger_meta SET value = '9'",
            "DELETE FROM ledger_meta",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="append-only|protected"):
                database.execute(statement)

    assert store.diagnostics() == before


def test_diagnostics_checks_the_ledger_before_sweeping(tmp_path):
    clock = FakeClock(0.0)
    store = make_store(tmp_path, cases=1, retries=0, clock=clock)
    store.claim("host-a", lease_s=10)
    (tmp_path / "plan.json").write_text('{"cases": "edited"}\n', encoding="utf-8")
    clock.now = 100.0
    before = store.path.read_bytes()

    with pytest.raises(ClaimError, match="different campaign plan"):
        store.diagnostics()
    assert store.path.read_bytes() == before


def test_retry_budget_passed_through_the_api_is_recorded_once(tmp_path):
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    store = ClaimStore(tmp_path)
    store.initialize(1)

    claim = store.claim("host-a", lease_s=60, retries=3)

    store.complete(claim, "host-a", INFRASTRUCTURE, bundle_for(tmp_path, claim, INFRASTRUCTURE), retries=3)
    with pytest.raises(ClaimError, match="retry budget is 3"):
        store.claim("host-b", lease_s=60, retries=0)
    assert store.diagnostics()["retries"] == 3


def test_invalid_result_files_and_status_mismatches_are_refused(tmp_path):
    store = make_store(tmp_path, cases=1)
    claim = store.claim("host-a", lease_s=60)
    bundle = bundle_for(tmp_path, claim)
    (bundle / "result.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ClaimError, match="not a valid episode result"):
        store.complete(claim, "host-a", BEHAVIOR, bundle)
    (bundle / "result.json").write_text(BEHAVIOR.model_dump_json(), encoding="utf-8")
    record = {"bundle": "case-000001/attempt-000001/bundle", "metrics_sha256": None,
              "result_sha256": hashlib.sha256((bundle / "result.json").read_bytes()).hexdigest(),
              "status": "watchdog", "classification": "infrastructure", "score": 0, "reason": None}
    with pytest.raises(ClaimError, match="disagrees with the ledger"):
        verified_attempt(tmp_path, record)
