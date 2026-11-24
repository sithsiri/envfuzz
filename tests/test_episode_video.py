import json

from envfuzz.episode_video import (
    export_presentation_archive,
    finish_video_capture,
    start_video_capture,
)


def test_video_capture_reports_unavailable_in_headless_host(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    capture = start_video_capture(tmp_path / "episode")

    metadata = finish_video_capture(tmp_path / "episode", capture)

    assert metadata["status"] == "unavailable"
    assert (tmp_path / "episode" / "video.json").is_file()
    assert not (tmp_path / "episode" / "episode.mp4").exists()


def test_presentation_archive_copies_marked_bundles(tmp_path):
    root = tmp_path / "campaign"
    root.mkdir()
    bundle = root / "case-000378" / "attempt-000001" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "result.json").write_text("{}")
    (bundle / "episode.mp4").write_text("video")
    (root / "manual-reviews.json").write_text(json.dumps({
        "schema_version": "1",
        "reviews": [{
            "campaign": "campaign",
            "case": 378,
            "attempt": 1,
            "bundle": "case-000378/attempt-000001/bundle",
            "observed_status": "mission_failed",
            "observed_classification": "behavior",
            "review_status": "confirmed",
            "presentation_candidate": True,
            "video": "episode.mp4",
        }],
    }))
    archive = tmp_path / "presentation"

    report = export_presentation_archive(root, archive)

    assert report["cases"][0]["case"] == 378
    copied = archive / "case-000378" / "original"
    assert (copied / "result.json").is_file()
    assert (copied / "episode.mp4").is_file()
    assert (archive / "presentation-manifest.json").is_file()
