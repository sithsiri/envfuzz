"""Human failure-review records and campaign candidate exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .campaign_index import refresh_index

MANUAL_REVIEW_SCHEMA_VERSION = "1"


class ManualReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = MANUAL_REVIEW_SCHEMA_VERSION
    campaign: str
    case: int = Field(ge=1)
    attempt: int = Field(ge=1)
    bundle: str
    observed_status: str
    observed_classification: Literal["behavior", "infrastructure", "interrupted"]
    review_status: Literal["unreviewed", "reviewing", "confirmed", "rejected"] = "unreviewed"
    failure_category: Literal[
        "planning", "localization", "perception", "control", "recovery",
        "infrastructure", "oracle", "scenario", "unknown",
    ] = "unknown"
    root_cause_category: Literal[
        "blocked_route", "localization_drift", "sensor_degradation", "stale_map",
        "stuck_recovery", "startup_stall", "resource_contention",
        "incorrect_result_classification", "unknown",
    ] = "unknown"
    confidence: Literal["low", "medium", "high"] | None = None
    reproducibility: Literal["unknown", "reproducible", "not_reproducible"] = "unknown"
    evidence_summary: str = ""
    presentation_candidate: bool = False
    video: str | None = None


def review_store(root: Path) -> Path:
    return root / "manual-reviews.json"


def load_manual_reviews(root: Path) -> dict[int, dict[int, ManualReview]]:
    path = review_store(root)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != MANUAL_REVIEW_SCHEMA_VERSION:
        raise ValueError("Unsupported manual-review schema version")
    reviews: dict[int, dict[int, ManualReview]] = {}
    for row in data.get("reviews", []):
        record = ManualReview.model_validate(row)
        reviews.setdefault(record.case, {})[record.attempt] = record
    return reviews


def save_manual_reviews(root: Path, reviews: dict[int, dict[int, ManualReview]]) -> Path:
    rows = [record.model_dump() for attempts in reviews.values() for record in attempts.values()]
    rows.sort(key=lambda row: (row["case"], row["attempt"]))
    path = review_store(root)
    path.write_text(json.dumps({
        "schema_version": MANUAL_REVIEW_SCHEMA_VERSION,
        "reviews": rows,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def upsert_manual_review(root: Path, review: ManualReview) -> Path:
    reviews = load_manual_reviews(root)
    reviews.setdefault(review.case, {})[review.attempt] = review
    return save_manual_reviews(root, reviews)


def export_failure_candidates(root: Path, output_json: Path, output_csv: Path) -> dict:
    root = root.resolve()
    refresh_index(root)
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    reviews = load_manual_reviews(root)
    candidates = []
    import sqlite3
    with sqlite3.connect(root / "results.sqlite3") as database:
        rows = database.execute(
            "SELECT case_id, attempt, layout, strategy, status, classification, bundle "
            "FROM attempts WHERE classification = 'behavior' AND status IN ('timeout', 'mission_failed') "
            "ORDER BY case_id, attempt"
        ).fetchall()
    for case, attempt, layout, strategy, status, classification, bundle in rows:
        case_data = plan["cases"][case - 1]
        challenge = case_data["scenario"].get("challenge", {})
        record = reviews.get(case, {}).get(attempt)
        result_path = (root / bundle / "result.json").resolve()
        try:
            result_path.relative_to(root)
            sim_duration = json.loads(result_path.read_text(encoding="utf-8")).get("sim_duration")
        except (OSError, ValueError, TypeError):
            sim_duration = None
        candidates.append({
            "campaign": root.name,
            "case": case,
            "attempt": attempt,
            "layout": layout,
            "strategy": strategy,
            "status": status,
            "sim_duration": sim_duration,
            "classification": classification,
            "bundle": bundle,
            "width_bin": challenge.get("width_bin"),
            "clutter_count": challenge.get("clutter_count"),
            "goal_distance_bin": challenge.get("goal_distance_bin"),
            "review_status": record.review_status if record else "unreviewed",
            "failure_category": record.failure_category if record else "unknown",
            "root_cause_category": record.root_cause_category if record else "unknown",
            "confidence": record.confidence if record else None,
            "reproducibility": record.reproducibility if record else "unknown",
            "presentation_candidate": record.presentation_candidate if record else False,
            "video": record.video if record else None,
        })
    report = {"schema_version": MANUAL_REVIEW_SCHEMA_VERSION, "campaign": root.name,
              "candidate_count": len(candidates), "candidates": candidates}
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    fields = list(candidates[0]) if candidates else [
        "campaign", "case", "attempt", "layout", "strategy", "status", "sim_duration", "classification",
        "bundle", "width_bin", "clutter_count", "goal_distance_bin", "review_status",
        "failure_category", "root_cause_category", "confidence", "reproducibility",
        "presentation_candidate", "video",
    ]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)
    return report
