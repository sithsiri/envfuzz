import json

import pytest
from pydantic import ValidationError

from envfuzz.manual_review import (
    MANUAL_REVIEW_SCHEMA_VERSION,
    ManualReview,
    export_failure_candidates,
    load_manual_reviews,
    upsert_manual_review,
)
from envfuzz.campaign_index import render_campaign_report


def _campaign(tmp_path):
    root = tmp_path / "campaign"
    root.mkdir()
    (root / "plan.json").write_text(json.dumps({
        "manifest": {"methods": ["uniform"], "layouts": ["apartment"]},
        "cases": [{
            "layout": "apartment",
            "method": "uniform",
            "scenario": {"challenge": {"width_bin": 2, "clutter_count": 4,
                                      "goal_distance_bin": 1}},
        }],
    }))
    from envfuzz.results import EpisodeResult
    timeout_bundle = root / "case-000001" / "attempt-000001" / "bundle"
    timeout_bundle.mkdir(parents=True)
    (timeout_bundle / "result.json").write_text(
        EpisodeResult(status="timeout", classification="behavior").model_dump_json(),
    )
    watchdog_bundle = root / "case-000001" / "attempt-000002" / "bundle"
    watchdog_bundle.mkdir(parents=True)
    (watchdog_bundle / "result.json").write_text(
        EpisodeResult(status="watchdog", classification="infrastructure").model_dump_json(),
    )
    return root


def test_export_separates_behavior_candidates_from_infrastructure(tmp_path):
    root = _campaign(tmp_path)

    report = export_failure_candidates(
        root, root / "review-candidates.json", root / "review-candidates.csv",
    )

    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["status"] == "timeout"
    assert candidate["classification"] == "behavior"
    assert candidate["review_status"] == "unreviewed"
    assert json.loads((root / "review-candidates.json").read_text())["candidate_count"] == 1
    assert "watchdog" not in (root / "review-candidates.csv").read_text()


def test_manual_review_round_trip_and_validation(tmp_path):
    root = _campaign(tmp_path)
    review = ManualReview(
        campaign=root.name,
        case=1,
        attempt=1,
        bundle="case-000001/attempt-000001/bundle",
        observed_status="timeout",
        observed_classification="behavior",
        review_status="confirmed",
        failure_category="planning",
        root_cause_category="blocked_route",
        confidence="high",
        reproducibility="reproducible",
        evidence_summary="Trajectory stops at a blocked corridor.",
        presentation_candidate=True,
        video="case-000001.mp4",
    )

    upsert_manual_review(root, review)

    loaded = load_manual_reviews(root)[1][1]
    assert loaded == review
    assert loaded.schema_version == MANUAL_REVIEW_SCHEMA_VERSION
    assert review.presentation_candidate


def test_campaign_report_shows_review_state_and_filters(tmp_path):
    root = _campaign(tmp_path)
    review = ManualReview(
        campaign=root.name,
        case=1,
        attempt=1,
        bundle="case-000001/attempt-000001/bundle",
        observed_status="timeout",
        observed_classification="behavior",
        review_status="confirmed",
        failure_category="planning",
        root_cause_category="blocked_route",
        confidence="high",
        reproducibility="reproducible",
    )
    upsert_manual_review(root, review)
    output = root / "report.html"

    render_campaign_report(
        root,
        output,
        review_status="confirmed",
        failure_category="planning",
        confidence="high",
    )

    html = output.read_text()
    assert "Failure category" in html
    assert "blocked_route" not in html
    assert "planning" in html
    assert "confirmed" in html


def test_manual_review_rejects_invalid_category(tmp_path):
    with pytest.raises(ValidationError):
        ManualReview(
            campaign="campaign",
            case=1,
            attempt=1,
            bundle="case/bundle",
            observed_status="timeout",
            observed_classification="behavior",
            failure_category="not-a-category",
        )
