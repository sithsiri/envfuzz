import json
import subprocess
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

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
from envfuzz.review_gui import ReplayJobs, candidate_detail, candidate_report, create_handler


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
        EpisodeResult(status="timeout", classification="behavior", sim_duration=12.5).model_dump_json(),
    )
    (timeout_bundle / "scenario.json").write_text(json.dumps({
        "challenge": {"width_bin": 2, "clutter_count": 4, "goal_distance_bin": 1},
        "goals": [],
        "objects": [],
        "restricted_zones": [],
        "seed": 42,
        "start": {"x": 0, "y": 0, "yaw": 0},
        "version": "1",
        "world": {"min_x": -5, "min_y": -5, "max_x": 5, "max_y": 5},
    }))
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
    assert candidate["sim_duration"] == 12.5
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


def test_review_gui_candidate_report_and_detail(tmp_path):
    root = _campaign(tmp_path)

    report = candidate_report(root)
    detail = candidate_detail(root, 1, 1)

    assert report["candidate_count"] == 1
    assert detail["candidate"]["status"] == "timeout"
    assert detail["result"]["status"] == "timeout"
    assert detail["scenario"]["challenge"]["clutter_count"] == 4
    assert detail["artifacts"]["logs"] is False
    assert detail["video"] is None


def test_review_gui_reports_video_status_metadata(tmp_path):
    root = _campaign(tmp_path)
    bundle = root / "case-000001" / "attempt-000001" / "bundle"

    for status, reason in (("available", None), ("unavailable", "No DISPLAY"), ("failed", "encoder error")):
        (bundle / "video.json").write_text(json.dumps({
            "schema_version": "1", "status": status, "video": "episode.mp4" if status == "available" else None,
            "replay_origin": "case-000001/attempt-000001/bundle", "reason": reason,
        }))
        detail = candidate_detail(root, 1, 1)
        assert detail["video"]["status"] == status
        assert detail["video"]["reason"] == reason
        assert detail["artifacts"]["video_metadata"] is True


def test_review_gui_replay_job_rejects_source_outside_campaign(tmp_path):
    root = _campaign(tmp_path)
    jobs = ReplayJobs(root, "local", "envfuzz:jazzy")

    job = jobs.create(1, 1, "../outside/bundle", 1, False)

    jobs.executor.shutdown(wait=True)
    final = jobs.get(job["id"])
    assert final["state"] == "failed"
    assert "outside the campaign" in final["error"]


def test_review_handler_rejects_artifact_outside_campaign(tmp_path):
    root = _campaign(tmp_path)
    jobs = ReplayJobs(root, "local", "envfuzz:jazzy")
    handler_type = create_handler(root, jobs)

    assert hasattr(handler_type, "do_GET")
    assert hasattr(handler_type, "do_POST")
    jobs.executor.shutdown(wait=True)


def test_review_handler_serves_candidate_detail(tmp_path):
    root = _campaign(tmp_path)
    jobs = ReplayJobs(root, "local", "envfuzz:jazzy")
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(root, jobs))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/cases/1/attempts/1") as response:
            detail = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        jobs.executor.shutdown(wait=True)

    assert detail["candidate"]["case"] == 1
    assert detail["candidate"]["attempt"] == 1
    assert detail["result"]["status"] == "timeout"


def test_review_handler_serves_loading_fallback_page(tmp_path):
    root = _campaign(tmp_path)
    jobs = ReplayJobs(root, "local", "envfuzz:jazzy")
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(root, jobs))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/") as response:
            page = response.read().decode("utf-8")
            cache_control = response.headers["Cache-Control"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        jobs.executor.shutdown(wait=True)

    assert "Could not load candidates" in page
    assert "Loading..." in page
    assert cache_control == "no-store"


def test_replay_job_reports_launch_failure(tmp_path, monkeypatch):
    root = _campaign(tmp_path)
    jobs = ReplayJobs(root, "local", "envfuzz:jazzy")
    monkeypatch.setattr(
        jobs,
        "_replay",
        lambda source, output, video, job_id: "launch_failed",
    )

    job = jobs.create(1, 1, "case-000001/attempt-000001/bundle", 1, False)
    jobs.executor.shutdown(wait=True)
    final = jobs.get(job["id"])

    assert final["state"] == "failed"
    assert "launch failed" in final["error"].lower()


def test_replay_jobs_survive_restart_and_are_case_scoped(tmp_path, monkeypatch):
    root = _campaign(tmp_path)
    jobs = ReplayJobs(root, "local", "envfuzz:jazzy")
    monkeypatch.setattr(jobs, "_replay", lambda source, output, video, job_id: "timeout")
    first = jobs.create(1, 1, "case-000001/attempt-000001/bundle", 1, False)
    jobs.executor.shutdown(wait=True)
    expected = jobs.get(first["id"])

    restarted = ReplayJobs(root, "local", "envfuzz:jazzy")
    assert restarted.get(first["id"]) == expected
    assert restarted.all(case=1, attempt=1) == [expected]
    assert restarted.all(case=2) == []
    restarted.executor.shutdown(wait=True)


def test_replay_jobs_mark_active_jobs_interrupted_after_restart(tmp_path):
    root = _campaign(tmp_path)
    store = root / "manual-reviews" / ReplayJobs.STORE_NAME
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"schema_version": "1", "jobs": [{
        "id": "active", "case": 1, "attempt": 1, "bundle": "case-000001/attempt-000001/bundle",
        "repeats": 1, "video": False, "state": "running", "results": [], "error": None,
    }]}))

    jobs = ReplayJobs(root, "local", "envfuzz:jazzy")

    assert jobs.get("active")["state"] == "interrupted"
    assert "restarted" in jobs.get("active")["error"]
    jobs.executor.shutdown(wait=True)


def test_video_replay_uses_isolated_video_image(tmp_path, monkeypatch):
    root = _campaign(tmp_path)
    source = root / "case-000001" / "attempt-000001" / "bundle"
    (source / "run.json").write_text(json.dumps({"watchdog": 30}), encoding="utf-8")
    output = root / "manual-reviews" / "case-000001" / "job-repeat-001" / "bundle"
    commands = []
    jobs = ReplayJobs(root, "local", "envfuzz:jazzy", "envfuzz:jazzy-video")
    jobs.jobs["job"] = {"state": "running"}

    class Process:
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def popen(command, **_options):
        commands.append(command)
        output.mkdir(parents=True)
        (output / "result.json").write_text('{"status":"completed"}', encoding="utf-8")
        return Process()

    monkeypatch.setattr("envfuzz.review_gui.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr("envfuzz.review_gui.subprocess.Popen", popen)

    status = jobs._replay(source, output, True, "job")

    assert status == "completed"
    assert "envfuzz:jazzy-video" in commands[0]
    assert "--video" in commands[0]
    assert "--network" not in commands[0]
    assert not any(".X11-unix" in argument for argument in commands[0])
    jobs.executor.shutdown(wait=True)
