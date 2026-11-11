"""Durable shared-filesystem job claims for multi-host campaign workers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
import time
from pathlib import Path

from .results import EpisodeResult


@dataclass(frozen=True)
class JobClaim:
    case_id: int
    attempt: int


class ClaimStore:
    """SQLite-backed claims; all workers must use the same campaign directory."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "claims.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA foreign_keys=ON")
        return database

    def initialize(self, cases: int) -> None:
        digest = hashlib.sha256((self.root / "plan.json").read_bytes()).hexdigest()
        with self._connect() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    case_id INTEGER PRIMARY KEY, plan_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT, lease_expires REAL,
                    authoritative_attempt INTEGER
                );
                CREATE TABLE IF NOT EXISTS job_attempts (
                    case_id INTEGER NOT NULL, attempt INTEGER NOT NULL, worker_id TEXT NOT NULL,
                    claimed_at REAL NOT NULL, finished_at REAL, status TEXT, classification TEXT,
                    bundle TEXT, PRIMARY KEY (case_id, attempt),
                    FOREIGN KEY (case_id) REFERENCES jobs(case_id)
                );
            """)
            known = database.execute("SELECT DISTINCT plan_sha256 FROM jobs").fetchall()
            if known and known != [(digest,)]:
                raise ValueError("The durable claim ledger belongs to a different campaign plan")
            for case_id in range(1, cases + 1):
                database.execute(
                    "INSERT OR IGNORE INTO jobs(case_id, plan_sha256, state) VALUES (?, ?, 'queued')",
                    (case_id, digest),
                )

    def claim(self, worker_id: str, lease_s: float, retries: int) -> JobClaim | None:
        if lease_s <= 0 or retries < 0:
            raise ValueError("Lease duration must be positive and retries must be nonnegative")
        now = time.time()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("""
                SELECT case_id, attempts FROM jobs
                WHERE attempts < ? AND (state = 'queued' OR (state = 'claimed' AND lease_expires < ?))
                ORDER BY case_id LIMIT 1
            """, (retries + 1, now)).fetchone()
            if row is None:
                database.execute("COMMIT")
                return None
            case_id, attempts = row
            attempt = attempts + 1
            changed = database.execute("""
                UPDATE jobs SET state='claimed', attempts=?, lease_owner=?, lease_expires=?
                WHERE case_id=? AND (state='queued' OR (state='claimed' AND lease_expires < ?))
            """, (attempt, worker_id, now + lease_s, case_id, now)).rowcount
            if not changed:
                database.execute("ROLLBACK")
                return None
            database.execute(
                "INSERT INTO job_attempts(case_id, attempt, worker_id, claimed_at) VALUES (?, ?, ?, ?)",
                (case_id, attempt, worker_id, now),
            )
            database.execute("COMMIT")
            return JobClaim(case_id, attempt)

    def complete(self, claim: JobClaim, worker_id: str, result: EpisodeResult, bundle: Path, retries: int) -> bool:
        """Record an immutable attempt and return whether it became authoritative."""
        now = time.time()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute("""
                UPDATE job_attempts SET finished_at=?, status=?, classification=?, bundle=?
                WHERE case_id=? AND attempt=? AND worker_id=?
            """, (now, result.status, result.classification, bundle.relative_to(self.root).as_posix(),
                  claim.case_id, claim.attempt, worker_id))
            job = database.execute(
                "SELECT attempts, authoritative_attempt FROM jobs WHERE case_id=?", (claim.case_id,)
            ).fetchone()
            if job is None:
                database.execute("ROLLBACK")
                raise ValueError("Unknown claimed job")
            attempts, authoritative = job
            accepted = authoritative is None and result.classification == "behavior"
            if accepted:
                database.execute("""
                    UPDATE jobs SET state='completed', authoritative_attempt=?, lease_owner=NULL, lease_expires=NULL
                    WHERE case_id=? AND authoritative_attempt IS NULL
                """, (claim.attempt, claim.case_id))
            elif authoritative is None and attempts >= retries + 1:
                database.execute("""
                    UPDATE jobs SET state='completed', authoritative_attempt=?, lease_owner=NULL, lease_expires=NULL
                    WHERE case_id=? AND authoritative_attempt IS NULL
                """, (claim.attempt, claim.case_id))
                accepted = True
            elif authoritative is None:
                database.execute("UPDATE jobs SET state='queued', lease_owner=NULL, lease_expires=NULL WHERE case_id=?",
                                 (claim.case_id,))
            database.execute("COMMIT")
            return accepted

    def complete_count(self) -> int:
        with self._connect() as database:
            return database.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0]
