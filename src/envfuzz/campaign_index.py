"""Index existing campaign attempts and render static summaries."""

from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .results import EpisodeResult
from .scenario import Scenario


def refresh_index(root: Path) -> int:
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    with sqlite3.connect(root / "results.sqlite3") as database:
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
        return database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]


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
    with sqlite3.connect(root / "results.sqlite3") as database:
        outcomes = database.execute("SELECT classification, COUNT(*) FROM attempts GROUP BY classification").fetchall()
        rows = database.execute(
            "SELECT case_id, attempt, layout, strategy, status, score, bundle FROM attempts a"
            + where + " ORDER BY score DESC, case_id, attempt", values,
        ).fetchall()
    counts = "".join(f"<li>{escape(category)}: {count}</li>" for category, count in outcomes)
    body = []
    for case, attempt, layout, method, status, score, bundle in rows:
        target = (root / bundle / "result.json").resolve()
        href = quote(os.path.relpath(target, output.resolve().parent))
        cells = (case, attempt, layout, method, status, score)
        body.append("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells)
                    + f'<td><a href="{href}">Result</a></td></tr>')
    headings = "".join(f"<th>{name}</th>" for name in ("Case", "Attempt", "Layout", "Strategy", "Status", "Score", "Bundle"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>EnvFuzz campaign</title>'
        "<h1>EnvFuzz campaign</h1><h2>All attempts</h2>"
        f"<ul>{counts}</ul><h2>Matching results</h2><table><thead><tr>{headings}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></html>\n",
        encoding="utf-8",
    )
