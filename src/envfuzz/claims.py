"""Durable shared-filesystem job claims for multi-host campaign workers.

The ledger is a SQLite file in the campaign directory. Every claim is a numbered
attempt under a bounded lease. Attempt rows are append-only: an attempt is
inserted when claimed and finished exactly once. The first behavior result
committed for a job becomes its authoritative result and never changes; every
other attempt stays visible for audit.

Each claim charges the job's retry budget. An attempt that reports
``launch_failed`` (the episode never started) refunds its charge, so a host that
cannot launch episodes never exhausts a job; workers bound such loops with their
own consecutive-failure limit.

The ledger uses SQLite's default rollback journal instead of WAL, because WAL
requires shared memory and does not work across hosts on a network filesystem.
Wall-clock time is used only for lease watchdogs, never for episode logic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
import socket
import sqlite3
import time

from .results import EpisodeResult

LEDGER_SCHEMA_VERSION = "3"
LEDGER_FILENAME = "claims.sqlite3"
BUSY_TIMEOUT_S = 30.0
MAX_LEASE_S = 86_400.0
WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
JOB_STATES = ("queued", "claimed", "completed", "exhausted")
SQLITE_HEADER = b"SQLite format 3\x00"
WAL_FORMAT = 2
UNVERSIONED = ("This claim ledger uses an unsupported, unversioned schema. It was not created by this "
               "envfuzz release; start the durable campaign in a new directory.")

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS approved_workers (
        worker_id TEXT PRIMARY KEY, approved_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS jobs (
        case_id INTEGER PRIMARY KEY, plan_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('queued', 'claimed', 'completed', 'exhausted')),
        attempts INTEGER NOT NULL DEFAULT 0, budget_used INTEGER NOT NULL DEFAULT 0,
        lease_owner TEXT, lease_expires REAL, authoritative_attempt INTEGER
    );
    CREATE TABLE IF NOT EXISTS job_attempts (
        case_id INTEGER NOT NULL, attempt INTEGER NOT NULL, worker_id TEXT NOT NULL,
        host TEXT NOT NULL, claimed_at REAL NOT NULL, lease_expires REAL NOT NULL,
        finished_at REAL, status TEXT, classification TEXT, score INTEGER, reason TEXT,
        bundle TEXT, result_sha256 TEXT, metrics_sha256 TEXT,
        PRIMARY KEY (case_id, attempt), FOREIGN KEY (case_id) REFERENCES jobs(case_id)
    );
    CREATE TRIGGER IF NOT EXISTS job_attempts_append_only BEFORE UPDATE ON job_attempts
    WHEN OLD.finished_at IS NOT NULL OR NEW.case_id IS NOT OLD.case_id
        OR NEW.attempt IS NOT OLD.attempt OR NEW.worker_id IS NOT OLD.worker_id
        OR NEW.host IS NOT OLD.host OR NEW.claimed_at IS NOT OLD.claimed_at
        OR NEW.lease_expires IS NOT OLD.lease_expires
    BEGIN SELECT RAISE(ABORT, 'job attempts are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS job_attempts_no_delete BEFORE DELETE ON job_attempts
    BEGIN SELECT RAISE(ABORT, 'job attempts are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS job_attempts_insert_once BEFORE INSERT ON job_attempts
    WHEN EXISTS (SELECT 1 FROM job_attempts WHERE case_id = NEW.case_id AND attempt = NEW.attempt)
    BEGIN SELECT RAISE(ABORT, 'job attempts are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS jobs_authoritative_once BEFORE UPDATE OF authoritative_attempt ON jobs
    WHEN OLD.authoritative_attempt IS NOT NULL
        AND NEW.authoritative_attempt IS NOT OLD.authoritative_attempt
    BEGIN SELECT RAISE(ABORT, 'an authoritative result is already committed'); END;
    CREATE TRIGGER IF NOT EXISTS jobs_identity_protected BEFORE UPDATE ON jobs
    WHEN NEW.case_id IS NOT OLD.case_id OR NEW.plan_sha256 IS NOT OLD.plan_sha256
        OR NEW.attempts < OLD.attempts
    BEGIN SELECT RAISE(ABORT, 'jobs are protected'); END;
    CREATE TRIGGER IF NOT EXISTS jobs_insert_once BEFORE INSERT ON jobs
    WHEN EXISTS (SELECT 1 FROM jobs WHERE case_id = NEW.case_id)
    BEGIN SELECT RAISE(ABORT, 'jobs are protected'); END;
    CREATE TRIGGER IF NOT EXISTS jobs_no_delete BEFORE DELETE ON jobs
    BEGIN SELECT RAISE(ABORT, 'jobs are protected'); END;
    CREATE TRIGGER IF NOT EXISTS ledger_meta_insert_once BEFORE INSERT ON ledger_meta
    WHEN EXISTS (SELECT 1 FROM ledger_meta WHERE key = NEW.key)
    BEGIN SELECT RAISE(ABORT, 'ledger metadata is protected'); END;
    CREATE TRIGGER IF NOT EXISTS ledger_meta_no_update BEFORE UPDATE ON ledger_meta
    BEGIN SELECT RAISE(ABORT, 'ledger metadata is protected'); END;
    CREATE TRIGGER IF NOT EXISTS ledger_meta_no_delete BEFORE DELETE ON ledger_meta
    BEGIN SELECT RAISE(ABORT, 'ledger metadata is protected'); END;
"""

_ATTEMPT_FIELDS = (
    "attempt", "worker_id", "host", "claimed_at", "lease_expires", "finished_at", "status",
    "classification", "score", "reason", "bundle", "result_sha256", "metrics_sha256",
)
_RESULT_FIELDS = ("attempt", "status", "classification", "score", "reason", "bundle",
                  "result_sha256", "metrics_sha256")


class ClaimError(ValueError):
    """A claim ledger operation was refused."""


@dataclass(frozen=True)
class JobClaim:
    case_id: int
    attempt: int
    worker_id: str = ""
    plan_sha256: str = ""
    lease_expires: float = 0.0


def plan_digest(root: Path) -> str:
    path = root / "plan.json"
    if path.is_symlink():
        raise ClaimError(f"{path} is a symlink; the frozen plan must be a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or WORKER_ID_PATTERN.fullmatch(worker_id) is None:
        raise ClaimError("Worker IDs use 1-64 letters, digits, '.', '_' or '-' and start alphanumeric")
    return worker_id


def _validate_lease(lease_s: float) -> None:
    if not (math.isfinite(lease_s) and 0 < lease_s <= MAX_LEASE_S):
        raise ValueError(f"Lease duration must be positive, finite and at most {MAX_LEASE_S:g} s")


def file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _read_result(path: Path) -> EpisodeResult:
    try:
        return EpisodeResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ClaimError(f"{path} is not a valid episode result: {error}") from error


def verified_attempt(root: Path, record: dict) -> tuple[Path | None, EpisodeResult]:
    """Return a finished attempt's bundle and result after checking them against the ledger.

    An attempt recorded without a bundle (it never produced one) is rebuilt from the ledger
    row. Otherwise ``result.json`` and ``metrics.json`` must still hash to the recorded values
    and the result must agree with the recorded status and classification.
    """
    if record["bundle"] is None:
        return None, EpisodeResult(status=record["status"], classification=record["classification"],
                                   score=record["score"] or 0, reason=record["reason"])
    if record["result_sha256"] is None:
        raise ClaimError(f"{record['bundle']} was recorded without a result.json hash")
    root = root.resolve()
    bundle = (root / record["bundle"]).resolve()
    if not bundle.is_relative_to(root):
        raise ClaimError(f"Recorded bundle {record['bundle']} is outside the campaign")
    for name, recorded in (("result.json", record["result_sha256"]), ("metrics.json", record["metrics_sha256"])):
        if file_sha256(bundle / name) != recorded:
            raise ClaimError(f"{record['bundle']}/{name} changed after the ledger recorded it")
    result = _read_result(bundle / "result.json")
    if (result.status, result.classification) != (record["status"], record["classification"]):
        raise ClaimError(f"{record['bundle']}/result.json disagrees with the ledger "
                         f"({result.status}/{result.classification} vs "
                         f"{record['status']}/{record['classification']})")
    return bundle, result


class ClaimStore:
    """SQLite-backed claims; all workers must use the same campaign directory."""

    def __init__(self, root: Path, *, clock: Callable[[], float] | None = None,
                 host: str | None = None) -> None:
        self.root = root
        self.path = root / LEDGER_FILENAME
        self._clock = clock
        self.host = host or socket.gethostname()

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.time()

    # Connections ---------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA recursive_triggers=ON")
        return database

    def _ledger_error(self, error: sqlite3.Error) -> ClaimError:
        return ClaimError(
            f"Claim ledger {self.path} is unavailable: {error}. A locked ledger is held by another "
            "worker; retry later. Persistent errors usually mean the shared filesystem lacks "
            "working POSIX locks or the file is damaged."
        )

    def _check_file(self) -> None:
        """Refuse symlinks, missing files, non-SQLite files and WAL ledgers without opening them."""
        if self.path.is_symlink():
            raise ClaimError(f"{self.path} is a symlink; the claim ledger must be a regular file")
        if not self.path.is_file():
            raise ClaimError(f"No claim ledger in {self.root}. Run campaign-approve first.")
        try:
            with self.path.open("rb") as handle:
                header = handle.read(len(SQLITE_HEADER) + 4)
        except OSError as error:
            raise ClaimError(f"Claim ledger {self.path} is unavailable: {error}") from error
        if not header:
            return
        if not header.startswith(SQLITE_HEADER):
            raise ClaimError(f"Claim ledger {self.path} is unavailable: it is not an SQLite database")
        if WAL_FORMAT in header[len(SQLITE_HEADER) + 2:]:
            raise ClaimError(UNVERSIONED)

    @staticmethod
    def _check_schema(database: sqlite3.Connection) -> None:
        if database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_meta'").fetchone() is None:
            raise ClaimError(UNVERSIONED)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._check_file()
        try:
            database = self._connect()
        except sqlite3.Error as error:
            raise self._ledger_error(error) from error
        try:
            database.execute("BEGIN IMMEDIATE")
            try:
                self._check_schema(database)
                yield database
            except BaseException:
                if database.in_transaction:
                    database.execute("ROLLBACK")
                raise
            database.execute("COMMIT")
        except sqlite3.Error as error:
            raise self._ledger_error(error) from error
        finally:
            database.close()

    def _open_read_only(self) -> sqlite3.Connection:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        database = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_S, isolation_level=None)
        try:
            database.execute("BEGIN")
            database.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        except sqlite3.Error:
            database.close()
            raise
        return database

    def _recover_with_read_write_open(self) -> None:
        """Let one read-write connection roll back the hot journal of a killed writer."""
        with closing(self._connect()) as database:
            database.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()

    @contextmanager
    def _read_only(self) -> Iterator[sqlite3.Connection]:
        self._check_file()
        try:
            try:
                database = self._open_read_only()
            except sqlite3.OperationalError:
                self._recover_with_read_write_open()
                database = self._open_read_only()
            try:
                self._check_schema(database)
                yield database
                database.execute("COMMIT")
            finally:
                database.close()
        except sqlite3.Error as error:
            raise self._ledger_error(error) from error

    # Metadata ------------------------------------------------------------------------------

    @staticmethod
    def _meta(database: sqlite3.Connection) -> dict[str, str]:
        return dict(database.execute("SELECT key, value FROM ledger_meta").fetchall())

    @staticmethod
    def _check_meta(meta: dict[str, str], digest: str) -> None:
        if meta.get("ledger_version") != LEDGER_SCHEMA_VERSION:
            raise ClaimError(f"Claim ledger version {meta.get('ledger_version')} is not supported")
        if meta["plan_sha256"] != digest:
            raise ClaimError("The durable claim ledger belongs to a different campaign plan")

    @staticmethod
    def _check_cases(meta: dict[str, str], cases: int) -> None:
        if int(meta["cases"]) != cases:
            raise ClaimError(f"The claim ledger case count {meta['cases']} differs from the plan ({cases})")

    def _max_attempts(self, database: sqlite3.Connection, retries: int | None, *, record: bool = False) -> int:
        """Return the attempt budget; a budget first given here is recorded when ``record`` is set."""
        recorded = self._meta(database).get("retries")
        if recorded is None:
            if retries is None:
                raise ClaimError("The ledger has no retry budget. Run campaign-approve with --retries.")
            if retries < 0:
                raise ValueError("Retries must be nonnegative")
            if record:
                database.execute("INSERT INTO ledger_meta VALUES ('retries', ?)", (str(retries),))
            return retries + 1
        if retries is not None and int(recorded) != retries:
            raise ClaimError(f"The ledger retry budget is {recorded}, not {retries}")
        return int(recorded) + 1

    def check_can_create(self) -> None:
        """Refuse to start a new ledger where single-host results or a symlinked ledger exist."""
        if self.path.is_symlink():
            raise ClaimError(f"{self.path} is a symlink; the claim ledger must be a regular file")
        if not self.path.exists() and (any(self.root.glob("case-*")) or (self.root / "results.csv").exists()):
            raise ClaimError(f"{self.root} already holds single-host campaign results (case-* folders or "
                             "results.csv). Use a new directory for durable workers, or finish this one "
                             "with envfuzz campaign.")

    def initialize(self, cases: int, *, retries: int | None = None) -> None:
        """Create or join the ledger for the frozen plan and record the retry budget once."""
        if retries is not None and retries < 0:
            raise ValueError("Retries must be nonnegative")
        self.check_can_create()
        digest = plan_digest(self.root)
        if self.path.exists():
            self._check_file()
        try:
            with closing(self._connect()) as database:
                tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and "ledger_meta" not in tables:
                    raise ClaimError(UNVERSIONED)
                database.executescript(_SCHEMA)
        except sqlite3.Error as error:
            raise self._ledger_error(error) from error
        with self._transaction() as database:
            meta = self._meta(database)
            if not meta:
                database.executemany("INSERT INTO ledger_meta VALUES (?, ?)", [
                    ("ledger_version", LEDGER_SCHEMA_VERSION), ("plan_sha256", digest),
                    ("cases", str(cases)),
                ])
                meta = self._meta(database)
            self._check_meta(meta, digest)
            self._check_cases(meta, cases)
            if retries is not None:
                self._max_attempts(database, retries, record=True)
            database.executemany(
                "INSERT INTO jobs(case_id, plan_sha256, state) SELECT ?, ?, 'queued' "
                "WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE case_id = ?)",
                [(case_id, digest, case_id) for case_id in range(1, cases + 1)],
            )

    def join(self, cases: int, *, retries: int | None = None) -> None:
        """Check, without writing, that the ledger matches the plan, case count and retry budget."""
        self._check_file()
        digest = plan_digest(self.root)
        with self._read_only() as database:
            meta = self._meta(database)
            self._check_meta(meta, digest)
            self._check_cases(meta, cases)
            self._max_attempts(database, retries)

    # Approval ------------------------------------------------------------------------------

    def approve(self, worker_ids: Iterable[str]) -> None:
        approved = [validate_worker_id(worker_id) for worker_id in worker_ids]
        with self._transaction() as database:
            now = self._now()
            database.executemany(
                "INSERT OR IGNORE INTO approved_workers VALUES (?, ?)",
                [(worker_id, now) for worker_id in approved],
            )

    def approved_workers(self) -> list[str]:
        with self._read_only() as database:
            return [row[0] for row in database.execute(
                "SELECT worker_id FROM approved_workers ORDER BY worker_id")]

    def require_approved(self, worker_id: str) -> None:
        """Refuse a worker unless the organizer added it to a nonempty allowlist."""
        validate_worker_id(worker_id)
        approved = self.approved_workers()
        if not approved:
            raise ClaimError("No workers are approved for this campaign. Run campaign-approve first.")
        if worker_id not in approved:
            raise ClaimError(f"Worker {worker_id!r} is not approved for this campaign")

    # Claims ---------------------------------------------------------------------------------

    @staticmethod
    def _sweep(database: sqlite3.Connection, now: float, max_attempts: int) -> None:
        """Close jobs whose last permitted lease expired (or lies beyond any valid lease) unfinished."""
        database.execute("""
            UPDATE jobs SET state='exhausted', lease_owner=NULL, lease_expires=NULL
            WHERE state='claimed' AND (lease_expires <= ? OR lease_expires > ?) AND budget_used >= ?
        """, (now, now + MAX_LEASE_S, max_attempts))

    def claim(self, worker_id: str, lease_s: float, retries: int | None = None) -> JobClaim | None:
        validate_worker_id(worker_id)
        _validate_lease(lease_s)
        digest = plan_digest(self.root)
        with self._transaction() as database:
            now = self._now()
            self._check_meta(self._meta(database), digest)
            max_attempts = self._max_attempts(database, retries, record=True)
            approved = {row[0] for row in database.execute("SELECT worker_id FROM approved_workers")}
            if approved and worker_id not in approved:
                raise ClaimError(f"Worker {worker_id!r} is not approved for this campaign")
            self._sweep(database, now, max_attempts)
            row = database.execute("""
                SELECT case_id, attempts FROM jobs
                WHERE plan_sha256 = ? AND budget_used < ?
                  AND (state = 'queued' OR (state = 'claimed' AND (lease_expires <= ? OR lease_expires > ?)))
                ORDER BY case_id LIMIT 1
            """, (digest, max_attempts, now, now + MAX_LEASE_S)).fetchone()
            if row is None:
                return None
            case_id, attempts = row
            attempt, expires = attempts + 1, now + lease_s
            database.execute("""
                UPDATE jobs SET state='claimed', attempts=?, budget_used=budget_used + 1, lease_owner=?,
                    lease_expires=? WHERE case_id=?
            """, (attempt, worker_id, expires, case_id))
            database.execute("""
                INSERT INTO job_attempts(case_id, attempt, worker_id, host, claimed_at, lease_expires)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (case_id, attempt, worker_id, self.host, now, expires))
            return JobClaim(case_id, attempt, worker_id, digest, expires)

    def _relative_bundle(self, case_id: int, bundle: Path) -> str:
        root = self.root.resolve()
        try:
            relative = bundle.resolve().relative_to(root / f"case-{case_id:06d}")
        except ValueError as error:
            raise ClaimError(f"Bundles must be inside case-{case_id:06d} of the campaign") from error
        return (Path(f"case-{case_id:06d}") / relative).as_posix()

    def _bundle_evidence(self, claim: JobClaim, result: EpisodeResult, bundle: Path,
                         from_bundle: bool) -> tuple[str | None, str | None, str | None]:
        """Check the attempt's own bundle and return its path and file hashes for the ledger."""
        if not from_bundle:
            if result.classification == "behavior":
                raise ClaimError("Behavior results must come from the attempt's own bundle")
            return None, None, None
        relative = self._relative_bundle(claim.case_id, bundle)
        result_path = bundle / "result.json"
        if not result_path.is_file():
            raise ClaimError(f"Attempt {claim.attempt} of case {claim.case_id} has no result.json in its bundle")
        written = _read_result(result_path)
        if (written.status, written.classification) != (result.status, result.classification):
            raise ClaimError(f"{relative}/result.json disagrees with the reported result")
        return relative, file_sha256(result_path), file_sha256(bundle / "metrics.json")

    def complete(self, claim: JobClaim, worker_id: str, result: EpisodeResult, bundle: Path,
                 retries: int | None = None, *, from_bundle: bool = True) -> bool:
        """Record an immutable attempt and return whether it became authoritative.

        Pass ``from_bundle=False`` when the attempt produced no bundle of its own; the ledger then
        records no bundle path or file hashes, so a stale folder can never be mistaken for it.
        """
        relative, digest, metrics = self._bundle_evidence(claim, result, bundle, from_bundle)
        with self._transaction() as database:
            now = self._now()
            max_attempts = self._max_attempts(database, retries, record=True)
            row = database.execute(
                "SELECT worker_id, finished_at FROM job_attempts WHERE case_id=? AND attempt=?",
                (claim.case_id, claim.attempt),
            ).fetchone()
            if row is None:
                raise ClaimError(f"Unknown claimed job {claim.case_id} attempt {claim.attempt}")
            if row[0] != worker_id:
                raise ClaimError(f"Attempt {claim.attempt} of case {claim.case_id} belongs to {row[0]}")
            if row[1] is not None:
                raise ClaimError(f"Attempt {claim.attempt} of case {claim.case_id} is already recorded")
            database.execute("""
                UPDATE job_attempts SET finished_at=?, status=?, classification=?, score=?, reason=?,
                    bundle=?, result_sha256=?, metrics_sha256=?
                WHERE case_id=? AND attempt=?
            """, (now, result.status, result.classification, result.score, result.reason, relative,
                  digest, metrics, claim.case_id, claim.attempt))
            return self._settle_job(database, claim, result, max_attempts)

    @staticmethod
    def _settle_job(database: sqlite3.Connection, claim: JobClaim, result: EpisodeResult,
                    max_attempts: int) -> bool:
        state, attempts, used, authoritative = database.execute(
            "SELECT state, attempts, budget_used, authoritative_attempt FROM jobs WHERE case_id=?",
            (claim.case_id,),
        ).fetchone()
        if authoritative is None and result.classification == "behavior":
            database.execute("""
                UPDATE jobs SET state='completed', authoritative_attempt=?, lease_owner=NULL,
                    lease_expires=NULL WHERE case_id=?
            """, (claim.attempt, claim.case_id))
            return True
        if authoritative is None and state == "claimed" and claim.attempt == attempts:
            used -= int(result.status == "launch_failed")
            database.execute(
                "UPDATE jobs SET state=?, budget_used=?, lease_owner=NULL, lease_expires=NULL WHERE case_id=?",
                ("exhausted" if used >= max_attempts else "queued", used, claim.case_id),
            )
        return False

    # Reading --------------------------------------------------------------------------------

    def complete_count(self) -> int:
        with self._read_only() as database:
            return database.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0]

    def unfinished_count(self, retries: int | None = None) -> int:
        """Count jobs that a live lease or remaining budget could still finish."""
        with self._transaction() as database:
            self._sweep(database, self._now(), self._max_attempts(database, retries, record=True))
            return database.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued', 'claimed')").fetchone()[0]

    def finished_attempts(self) -> dict[int, dict]:
        """Read-only: each case's authoritative attempt number and its finished attempt records."""
        self._check_file()
        digest = plan_digest(self.root)
        with self._read_only() as database:
            self._check_meta(self._meta(database), digest)
            jobs = database.execute("SELECT case_id, authoritative_attempt FROM jobs ORDER BY case_id").fetchall()
            rows = database.execute(
                f"SELECT case_id, {', '.join(_RESULT_FIELDS)} FROM job_attempts "
                "WHERE finished_at IS NOT NULL ORDER BY case_id, attempt"
            ).fetchall()
        entries = {case_id: {"authoritative_attempt": authoritative, "finished": []}
                   for case_id, authoritative in jobs}
        for case_id, *values in rows:
            entries[case_id]["finished"].append(dict(zip(_RESULT_FIELDS, values)))
        return entries

    def authoritative_results(self) -> dict[int, dict | None]:
        """Map every case to its authoritative attempt record, or None, without writing the ledger."""
        return {
            case_id: next((record for record in entry["finished"]
                           if record["attempt"] == entry["authoritative_attempt"]), None)
            for case_id, entry in self.finished_attempts().items()
        }

    def diagnostics(self) -> dict:
        """Return the organizer's view of every job, attempt, and authoritative choice."""
        self._check_file()
        digest = plan_digest(self.root)
        with self._transaction() as database:
            now = self._now()
            meta = self._meta(database)
            self._check_meta(meta, digest)
            max_attempts = self._max_attempts(database, None) if "retries" in meta else None
            if max_attempts is not None:
                self._sweep(database, now, max_attempts)
            approved = [row[0] for row in database.execute(
                "SELECT worker_id FROM approved_workers ORDER BY worker_id")]
            jobs = database.execute("""
                SELECT case_id, state, attempts, budget_used, lease_owner, lease_expires, authoritative_attempt
                FROM jobs ORDER BY case_id
            """).fetchall()
            attempts = database.execute(
                f"SELECT case_id, {', '.join(_ATTEMPT_FIELDS)} FROM job_attempts ORDER BY case_id, attempt"
            ).fetchall()
        by_case: dict[int, list[dict]] = {}
        for case_id, *values in attempts:
            by_case.setdefault(case_id, []).append(dict(zip(_ATTEMPT_FIELDS, values)))
        rows = []
        for case_id, state, count, used, owner, expires, authoritative in jobs:
            history = by_case.get(case_id, [])
            for attempt in history:
                attempt["outcome"] = _outcome(attempt, authoritative, now)
                attempt["clock_skew_suspected"] = (attempt["finished_at"] is None
                                                   and attempt["lease_expires"] > now + MAX_LEASE_S)
            rows.append({
                "case_id": case_id, "state": state, "attempt_count": count, "budget_used": used,
                "lease_owner": owner, "lease_expires": expires, "authoritative_attempt": authoritative,
                "attempts": history,
            })
        return {
            "ledger_version": meta.get("ledger_version"), "plan_sha256": meta.get("plan_sha256"),
            "cases": int(meta["cases"]) if "cases" in meta else None,
            "retries": int(meta["retries"]) if "retries" in meta else None,
            "max_attempts": max_attempts, "approved_workers": approved,
            "counts": {state: sum(row["state"] == state for row in rows) for state in JOB_STATES},
            "jobs": rows,
        }


def _outcome(attempt: dict, authoritative: int | None, now: float) -> str:
    if attempt["attempt"] == authoritative:
        return "authoritative"
    if attempt["finished_at"] is not None:
        return "duplicate" if attempt["classification"] == "behavior" else "failed"
    return "running" if now < attempt["lease_expires"] <= now + MAX_LEASE_S else "lease_expired"
