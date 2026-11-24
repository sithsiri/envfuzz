"""Optional episode video capture and evidence archive helpers."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path


def start_video_capture(output: Path, replay_origin: str | None = None) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    display = os.environ.get("DISPLAY")
    ffmpeg = shutil.which("ffmpeg")
    temporary = output.with_name(output.name + ".capture.tmp.mkv")
    metadata = {
        "schema_version": "1",
        "status": "unavailable",
        "video": None,
        "replay_origin": replay_origin,
        "reason": None,
    }
    if not display:
        metadata["reason"] = "No DISPLAY is available for episode recording."
        return {"metadata": metadata, "process": None, "temporary": temporary}
    if not ffmpeg:
        metadata["reason"] = "ffmpeg is unavailable."
        return {"metadata": metadata, "process": None, "temporary": temporary}
    try:
        process = subprocess.Popen([
            ffmpeg,
            "-loglevel", "error",
            "-f", "x11grab",
            "-framerate", "30",
            "-i", display,
            "-an",
            "-y",
            str(temporary),
        ])
    except OSError as error:
        metadata["reason"] = str(error)
        return {"metadata": metadata, "process": None, "temporary": temporary}
    metadata["status"] = "recording"
    return {"metadata": metadata, "process": process, "temporary": temporary}


def finish_video_capture(bundle: Path, capture: dict) -> dict:
    process = capture["process"]
    temporary = capture["temporary"]
    metadata = capture["metadata"]
    if process is not None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        if temporary.is_file():
            video = bundle / "episode.mp4"
            try:
                subprocess.run([
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-loglevel", "error",
                    "-i", str(temporary),
                    "-c:v", "copy",
                    "-y",
                    str(video),
                ], check=True)
                temporary.unlink(missing_ok=True)
                metadata["status"] = "available"
                metadata["video"] = "episode.mp4"
            except (OSError, subprocess.CalledProcessError) as error:
                metadata["status"] = "failed"
                metadata["reason"] = str(error)
        else:
            metadata["status"] = "failed"
            metadata["reason"] = f"Capture process exited with code {process.returncode}."
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "video.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def export_presentation_archive(root: Path, archive: Path) -> dict:
    root = root.resolve()
    archive.mkdir(parents=True)
    review_path = root / "manual-reviews.json"
    if not review_path.is_file():
        raise ValueError("Campaign has no manual-reviews.json")
    reviews = json.loads(review_path.read_text(encoding="utf-8")).get("reviews", [])
    selected = [row for row in reviews if row.get("presentation_candidate")]
    if not selected:
        raise ValueError("No presentation candidates are marked")
    manifest = []
    import shutil as file_shutil
    for row in selected:
        case = int(row["case"])
        attempt = int(row["attempt"])
        source = root / row["bundle"]
        case_dir = archive / f"case-{case:06d}"
        case_dir.mkdir()
        if source.is_dir():
            file_shutil.copytree(source, case_dir / "original", dirs_exist_ok=True)
        video = row.get("video")
        if video:
            manifest.append({"case": case, "attempt": attempt,
                             "video": str(case_dir / "original" / video)})
        else:
            manifest.append({"case": case, "attempt": attempt, "video": None})
    (archive / "presentation-manifest.json").write_text(
        json.dumps({"schema_version": "1", "cases": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"schema_version": "1", "cases": manifest}
