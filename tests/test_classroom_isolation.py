import json
import os
import subprocess
from pathlib import Path

import pytest

from envfuzz.classroom import check_submission
from envfuzz.scenario import Scenario, save_scenario
from envfuzz.workers import WorkerLimits, run_worker


@pytest.fixture
def challenge(tmp_path):
    root = tmp_path / "challenge"
    root.mkdir()
    save_scenario(Scenario(), root / "fixture.yaml")
    manifest = root / "challenge.yaml"
    manifest.write_text(
        "id: test\ndifficulty: introductory\nscenario: fixture.yaml\nbaseline: baseline.json\n",
        encoding="utf-8",
    )
    submission = tmp_path / "submission.json"
    submission.write_text('{"challenge":"test","team":"test","edits":[]}', encoding="utf-8")
    return manifest, submission


@pytest.mark.parametrize(
    "body",
    [
        '{"challenge":',
        '{"challenge":"test","team":"test","edits":' + "[" * 220 + "]" * 220 + "}",
        '{"challenge":"test","team":"test","edits":[],"params":"../params.yaml"}',
        '{"challenge":"test","team":"test","edits":[],"sensor_topic":"/scan"}',
    ],
)
def test_invalid_submissions_are_rejected(challenge, body):
    manifest, submission = challenge
    submission.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        check_submission(manifest, submission)


@pytest.mark.parametrize("mode", ["relative", "absolute", "symlink"])
def test_organizer_files_cannot_escape_challenge_root(challenge, mode):
    manifest, submission = challenge
    outside = manifest.parent.parent / "outside.yaml"
    save_scenario(Scenario(), outside)
    if mode == "symlink":
        target = Path("link.yaml")
        (manifest.parent / target).symlink_to(outside)
    else:
        target = outside if mode == "absolute" else Path("../outside.yaml")
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("fixture.yaml", str(target)), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inside the challenge directory"):
        check_submission(manifest, submission)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX named pipes")
def test_nonregular_submission_is_rejected_without_waiting(challenge):
    manifest, submission = challenge
    submission.unlink()
    os.mkfifo(submission)
    with pytest.raises(ValueError, match="regular file"):
        check_submission(manifest, submission)


def test_organizer_worker_is_isolated(tmp_path, monkeypatch):
    bundle = tmp_path / "repeat" / "bundle"

    def fake_run(command, **kwargs):
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 1, stdout="")
        assert command[1] == "run"
        assert command[command.index("--network") + 1] == "none"
        assert command[command.index("--cpus") + 1] == "1.25"
        assert command[command.index("--memory") + 1] == "768m"
        assert "--read-only" in command
        assert "--privileged" not in command
        mounts = [command[index + 1] for index, option in enumerate(command) if option == "--mount"]
        assert mounts == [
            f"type=bind,src={bundle.resolve().parent / 'inputs'},dst=/inputs,readonly",
            f"type=bind,src={bundle.resolve().parent / 'outputs'},dst=/results",
        ]
        assert command[-6:] == [
            "--output", "/results/bundle", "--timeout", "20", "--watchdog", "30",
        ]
        assert kwargs["timeout"] == 50
        return subprocess.CompletedProcess(command, 125)

    monkeypatch.setattr("envfuzz.workers.subprocess.run", fake_run)
    result = run_worker(Scenario(), bundle, 20, 30, WorkerLimits(cpus=1.25, memory="768m"), read_only=True)
    assert result.classification == "infrastructure"
    metadata = json.loads((bundle.parent / "worker.json").read_text(encoding="utf-8"))
    assert metadata["network"] == "none"
