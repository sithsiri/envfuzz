"""Index existing campaign attempts and render static summaries."""

from __future__ import annotations

from contextlib import closing
from html import escape
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .claims import LEDGER_FILENAME, ClaimStore, verified_attempt
from .results import EpisodeResult
from .scenario import Scenario

INDEX_TIMEOUT_S = 30.0


class CampaignIndexError(ValueError):
    """The derived campaign index could not be read or written."""


def index_error(root: Path, error: sqlite3.Error) -> CampaignIndexError:
    return CampaignIndexError(
        f"Campaign index {root / 'results.sqlite3'} is unavailable: {error}. It holds derived data only; "
        "wait for other readers to finish, or delete it and rerun the command to rebuild it."
    )


def refresh_index(root: Path) -> int:
    """Index campaign attempts in results.sqlite3 and return the number of indexed attempts.

    A durable campaign (one with a claim ledger) is rebuilt from the attempts the ledger
    recorded, using each attempt's hash-verified bundle. Other campaigns index every
    attempt folder that holds a result, as before.
    """
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    durable = _ledger_rows(root, plan) if (root / LEDGER_FILENAME).exists() else None
    try:
        with closing(sqlite3.connect(root / "results.sqlite3", timeout=INDEX_TIMEOUT_S)) as database:
            _create_schema(database)
            with database:
                if durable is not None:
                    _replace_rows(database, *durable)
                else:
                    _index_attempt_folders(database, root, plan)
            return database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    except sqlite3.Error as error:
        raise index_error(root, error) from error


def _ledger_rows(root: Path, plan: dict) -> tuple[list[tuple], list[tuple]]:
    """Return index rows for every attempt the ledger recorded, verified against its bundle."""
    attempts, tiers = [], []
    for case_id, entry in ClaimStore(root).finished_attempts().items():
        case = plan["cases"][case_id - 1]
        scenario_hash = Scenario.model_validate(case["scenario"]).content_hash()
        for record in entry["finished"]:
            _, result = verified_attempt(root, record)
            attempts.append((case_id, record["attempt"], case["layout"], case["method"], scenario_hash,
                             result.status, result.classification, result.score, result.wall_duration,
                             record["bundle"] or ""))
            tiers += [(case_id, record["attempt"], tier) for tier in sorted(set(result.tiers_hit))]
    return attempts, tiers


def _replace_rows(database: sqlite3.Connection, attempts: list[tuple], tiers: list[tuple]) -> None:
    """Rebuild the derived index rows of a durable campaign from the verified ledger rows."""
    database.execute("DELETE FROM tiers")
    database.execute("DELETE FROM attempts")
    database.executemany("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", attempts)
    database.executemany("INSERT INTO tiers VALUES (?, ?, ?)", tiers)


def _create_schema(database: sqlite3.Connection) -> None:
    database.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS attempts (
                case_id INTEGER NOT NULL, attempt INTEGER NOT NULL,
                layout TEXT NOT NULL, strategy TEXT NOT NULL, scenario_hash TEXT NOT NULL,
                status TEXT NOT NULL, classification TEXT NOT NULL, score INTEGER NOT NULL,
                wall_s REAL NOT NULL, bundle TEXT NOT NULL,
                PRIMARY KEY (case_id, attempt)
            );
            CREATE TABLE IF NOT EXISTS tiers (
                case_id INTEGER NOT NULL, attempt INTEGER NOT NULL, tier TEXT NOT NULL,
                PRIMARY KEY (case_id, attempt, tier),
                FOREIGN KEY (case_id, attempt) REFERENCES attempts (case_id, attempt) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS attempts_lookup ON attempts (strategy, score, scenario_hash);
            CREATE INDEX IF NOT EXISTS tier_lookup ON tiers (tier);
        """)


def _index_attempt_folders(database: sqlite3.Connection, root: Path, plan: dict) -> None:
    """Index every attempt folder of a single-host campaign that holds a result."""
    for index, case in enumerate(plan["cases"], 1):
        scenario_hash = Scenario.model_validate(case["scenario"]).content_hash()
        for attempt in sorted((root / f"case-{index:06d}").glob("attempt-*")):
            result_path = attempt / "result.json"
            if not result_path.is_file():
                result_path = attempt / "bundle" / "result.json"
            if not result_path.is_file():
                continue
            result = EpisodeResult.model_validate_json(result_path.read_text(encoding="utf-8"))
            number = int(attempt.name.removeprefix("attempt-"))
            database.execute("""
                INSERT OR REPLACE INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (index, number, case["layout"], case["method"], scenario_hash,
                  result.status, result.classification, result.score, result.wall_duration,
                  result_path.parent.relative_to(root).as_posix()))
            database.executemany(
                "INSERT INTO tiers VALUES (?, ?, ?)",
                [(index, number, tier) for tier in sorted(set(result.tiers_hit))],
            )


def render_campaign_report(
    root: Path, output: Path, *, strategy: str | None = None, tier: str | None = None,
    scenario_hash: str | None = None, minimum_score: int | None = None,
) -> None:
    refresh_index(root)
    filters, values = [], []
    for condition, value in (
        ("strategy = ?", strategy), ("scenario_hash = ?", scenario_hash), ("score >= ?", minimum_score),
        ("EXISTS (SELECT 1 FROM tiers WHERE tiers.case_id = a.case_id AND tiers.attempt = a.attempt AND tier = ?)", tier),
    ):
        if value is not None:
            filters.append(condition)
            values.append(value)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    try:
        with closing(sqlite3.connect(root / "results.sqlite3", timeout=INDEX_TIMEOUT_S)) as database:
            outcomes = database.execute(
                "SELECT classification, COUNT(*) FROM attempts GROUP BY classification").fetchall()
            rows = database.execute(
                "SELECT case_id, attempt, layout, strategy, status, score, bundle FROM attempts a"
                + where + " ORDER BY score DESC, case_id, attempt", values,
            ).fetchall()
    except sqlite3.Error as error:
        raise index_error(root, error) from error
    counts = "".join(f"<li>{escape(category)}: {count}</li>" for category, count in outcomes)
    body = []
    for case, attempt, layout, method, status, score, bundle in rows:
        link = "-"
        if bundle:
            target = (root / bundle / "result.json").resolve()
            link = f'<a href="{quote(os.path.relpath(target, output.resolve().parent))}">Result</a>'
        cells = (case, attempt, layout, method, status, score)
        body.append("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells)
                    + f"<td>{link}</td></tr>")
    headings = "".join(f"<th>{name}</th>" for name in ("Case", "Attempt", "Layout", "Strategy", "Status", "Score", "Bundle"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>EnvFuzz campaign</title>'
        "<h1>EnvFuzz campaign</h1><h2>All attempts</h2>"
        f"<ul>{counts}</ul><h2>Matching results</h2><table><thead><tr>{headings}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></html>\n",
        encoding="utf-8",
    )
