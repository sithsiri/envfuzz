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


def test_graphical_client_failure_marks_video_failed_without_changing_result(tmp_path):
    bundle = tmp_path / "episode"
    bundle.mkdir()
    (bundle / "result.json").write_text('{"status":"completed"}')
    (bundle / "logs.txt").write_text(
        "[ERROR] [gazebo_gui]: process has died [pid 42, exit code -11, "
        "cmd 'gz sim -g --gui-config /tmp/gui.config']\n"
    )
    temporary = tmp_path / "capture.mkv"
    temporary.write_bytes(b"capture")

    class Process:
        returncode = 0

        def send_signal(self, _signal):
            pass

        def wait(self, timeout=None):
            return 0

    metadata = finish_video_capture(bundle, {
        "metadata": {"schema_version": "1", "status": "recording", "video": None,
                     "replay_origin": "original", "reason": None},
        "process": Process(),
        "temporary": temporary,
        "graphical": True,
    })

    assert metadata["status"] == "failed"
    assert "graphical client" in metadata["reason"]
    assert json.loads((bundle / "result.json").read_text())["status"] == "completed"
    assert not (bundle / "episode.mp4").exists()


def test_headless_server_killed_during_shutdown_does_not_discard_video(tmp_path, monkeypatch):
    bundle = tmp_path / "episode"
    bundle.mkdir()
    (bundle / "logs.txt").write_text(
        "[WARNING] [launch]: user interrupted with ctrl-c (SIGINT)\n"
        "[ERROR] [gz-4]: process has died [pid 42, exit code -9, "
        "cmd 'gz sim -r -s --headless-rendering world.sdf']\n"
    )
    temporary = tmp_path / "capture.mkv"
    temporary.write_bytes(b"capture")

    class Process:
        returncode = 0

        def send_signal(self, _signal):
            pass

        def wait(self, timeout=None):
            return 0

    def encode(command, **_kwargs):
        from pathlib import Path
        Path(command[-1]).write_bytes(b"encoded")

    monkeypatch.setattr("envfuzz.episode_video.subprocess.run", encode)
    metadata = finish_video_capture(bundle, {
        "metadata": {"schema_version": "1", "status": "recording", "video": None,
                     "replay_origin": "original", "reason": None},
        "process": Process(),
        "temporary": temporary,
        "graphical": True,
    })

    assert metadata["status"] == "available"
    assert metadata["video"] == "episode.mp4"
    assert (bundle / "episode.mp4").read_bytes() == b"encoded"


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
