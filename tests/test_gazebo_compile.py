"""Gazebo episode orchestration with a fake mission worker (no ROS, Gazebo or child processes)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from envfuzz import gazebo
from envfuzz.gazebo import require_camera_capability, run_gazebo
from envfuzz.scenario import Pose, Scenario, ScenarioObject, Size, TimedMove, Zone
from envfuzz.scoring import mission_identity

FAKE_PID = 999_999


class FakeWorker:
    """Imitates the mission subprocess by writing worker files into the bundle directory."""

    def __init__(self, behaviour, returncode: int = 0, wait_error: BaseException | None = None):
        self.behaviour = behaviour
        self.returncode = returncode
        self.wait_error = wait_error
        self.command = None
        self.env = None
        self.pid = FAKE_PID

    def __call__(self, command, env, stdout, stderr, start_new_session):
        assert start_new_session is True
        self.command, self.env = command, env
        self.behaviour(Path(command[-2]), stdout)
        return self

    def wait(self, timeout):
        if self.wait_error is not None:
            raise self.wait_error
        return self.returncode


def _mission(status: str = "completed", duration: float = 4.0, telemetry: list[dict] | None = None):
    def behaviour(directory: Path, logs) -> None:
        logs.write("worker started\n")
        (directory / "mission.json").write_text(json.dumps({
            "status": status, "classification": "behavior", "sim_duration": duration}))
        rows = telemetry if telemetry is not None else [
            {"ground_truth": {"sim_time": 1.0, "x": 0.0, "y": 0.0}, "estimated": None}]
        (directory / "telemetry.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return behaviour


@pytest.fixture
def ros_ready(monkeypatch):
    """Pretend ROS is sourced and record terminations instead of signalling real process groups."""
    terminated = []
    _patch_which(monkeypatch, lambda name: "/opt/ros/bin/ros2" if name == "ros2" else None)
    monkeypatch.setattr(gazebo, "importlib", SimpleNamespace(
        util=SimpleNamespace(find_spec=lambda name: object())))
    monkeypatch.setattr(gazebo, "_terminate", terminated.append)
    for name in ("ENVFUZZ_LIDAR_NOISE_M", "ENVFUZZ_RECORD_CLIFF", "ENVFUZZ_LOCALIZATION_MAP",
                 "ENVFUZZ_WORKER_DOMAIN"):
        monkeypatch.delenv(name, raising=False)
    return terminated


def _patch_which(monkeypatch, which) -> None:
    """Replace shutil.which for envfuzz.gazebo only, keeping the real copyfile."""
    monkeypatch.setattr(gazebo, "shutil", SimpleNamespace(which=which, copyfile=shutil.copyfile))


def _patch_subprocess(monkeypatch, **overrides) -> None:
    """Replace subprocess members for envfuzz.gazebo only, leaving the global module intact."""
    proxy = SimpleNamespace(**{name: getattr(subprocess, name) for name in (
        "PIPE", "DEVNULL", "STDOUT", "TimeoutExpired", "Popen", "run")})
    for name, value in overrides.items():
        setattr(proxy, name, value)
    monkeypatch.setattr(gazebo, "subprocess", proxy)


def _use_worker(monkeypatch, worker: FakeWorker) -> FakeWorker:
    _patch_subprocess(monkeypatch, Popen=worker)
    return worker


def test_camera_capability_requires_nvidia_tooling(monkeypatch):
    # Arrange
    _patch_which(monkeypatch, lambda name: None)

    # Act / Assert
    with pytest.raises(OSError, match="requires an NVIDIA GPU"):
        require_camera_capability()


@pytest.mark.parametrize("returncode, stdout, ok", [(0, "RTX 4000\n", True), (0, "  \n", False),
                                                     (9, "RTX 4000\n", False)])
def test_camera_capability_probes_for_a_visible_gpu(monkeypatch, returncode, stdout, ok):
    # Arrange
    calls = []
    _patch_which(monkeypatch, lambda name: "/usr/bin/nvidia-smi")

    def probe(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    _patch_subprocess(monkeypatch, run=probe)

    # Act / Assert
    if ok:
        assert require_camera_capability() is None
    else:
        with pytest.raises(OSError, match="could not detect"):
            require_camera_capability()
    assert calls == [["/usr/bin/nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]]


def test_mission_without_goals_is_rejected_before_output_exists(tmp_path):
    # Act / Assert
    with pytest.raises(ValueError, match="at least one goal"):
        run_gazebo(Scenario(goals=[]), tmp_path / "run", timeout=1, watchdog=2)
    assert not (tmp_path / "run").exists()


def test_camera_profile_checks_gpu_before_creating_output(tmp_path, monkeypatch):
    # Arrange
    profile = tmp_path / "camera.yaml"
    profile.write_text("camera_profile: true\nnavigation_backend: rtabmap\n", encoding="utf-8")
    _patch_which(monkeypatch, lambda name: None)

    # Act / Assert
    with pytest.raises(OSError, match="NVIDIA"):
        run_gazebo(Scenario(), tmp_path / "run", timeout=1, watchdog=2, profile_file=profile)
    assert not (tmp_path / "run").exists()


def test_baseline_without_position_threshold_is_rejected(tmp_path):
    # Arrange
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schema_version": "1", "thresholds": {"metric": 0.2}}))

    # Act / Assert
    with pytest.raises(ValueError, match="Run calibration again"):
        run_gazebo(Scenario(), tmp_path / "run", timeout=1, watchdog=2, baseline_file=baseline)


def test_completed_worker_is_scored_against_matching_baseline(tmp_path, monkeypatch, ros_ready):
    # Arrange
    scenario = Scenario()
    params = tmp_path / "inputs" / "nav2.yaml"
    params.parent.mkdir()
    params.write_text("controller: {}\n", encoding="utf-8")
    baseline = tmp_path / "inputs" / "baseline.json"
    baseline.write_text(json.dumps({
        "schema_version": "1", "identity": mission_identity(scenario, params),
        "thresholds": {"position_error_m": 0.1}, "completed_trials": 3,
    }))
    oracle = tmp_path / "inputs" / "oracle.yaml"
    oracle.write_text("position_window_s: 0.2\n", encoding="utf-8")
    telemetry = [{"ground_truth": {"sim_time": t, "x": 0.1 * t, "y": 0.0},
                  "estimated": {"sim_time": t, "x": 0.1 * t + 0.5, "y": 0.0}} for t in (1.0, 1.1, 1.2, 1.3)]
    worker = _use_worker(monkeypatch, FakeWorker(_mission(telemetry=telemetry)))
    output = tmp_path / "run"

    # Act
    result = run_gazebo(scenario, output, timeout=9, watchdog=20, params_file=params,
                        baseline_file=baseline, oracle_file=oracle)

    # Assert
    assert result.status == "completed"
    assert result.tiers_hit == ["T2"] and result.score == 2
    assert result.baseline_sha256 is not None
    assert result.real_time_factor == pytest.approx(result.sim_duration / result.wall_duration)
    assert worker.command[-3:] == ["envfuzz.mission", str(output), "9"]
    assert (output / "nav2.yaml").read_text() == "controller: {}\n"
    assert json.loads((output / "baseline.json").read_text())["completed_trials"] == 3
    assert json.loads((output / "oracle.yaml").read_text())["position_window_s"] == 0.2
    assert not (output / "mission.json").exists()
    assert json.loads((output / "result.json").read_text())["score"] == 2
    assert ros_ready == [worker]


def test_restricted_zone_events_are_reported_without_a_baseline(tmp_path, monkeypatch, ros_ready):
    # Arrange
    zone = Zone(id="dock", polygon=[(0.5, -0.5), (1.5, -0.5), (1.5, 0.5), (0.5, 0.5)])
    scenario = Scenario(restricted_zones=[zone])
    telemetry = [{"ground_truth": {"sim_time": t, "x": x, "y": 0.0}, "estimated": None}
                 for t, x in ((1.0, 0.0), (2.0, 1.0), (3.0, 2.0))]
    _use_worker(monkeypatch, FakeWorker(_mission(telemetry=telemetry)))

    # Act
    result = run_gazebo(scenario, tmp_path / "run", timeout=9, watchdog=20)

    # Assert
    assert [(event.kind, event.details["zone"]) for event in result.events] == [("restricted_area", "dock")]
    assert result.score == 0  # unscored without a calibration baseline


def test_non_zero_worker_exit_is_an_infrastructure_failure(tmp_path, monkeypatch, ros_ready):
    # Arrange
    _use_worker(monkeypatch, FakeWorker(_mission(), returncode=3))

    # Act
    result = run_gazebo(Scenario(), tmp_path / "run", timeout=9, watchdog=20)

    # Assert
    assert (result.status, result.classification) == ("simulator_failed", "infrastructure")
    assert "code 3" in result.reason
    assert result.real_time_factor is None
    assert (tmp_path / "run" / "logs.txt").read_text() == "worker started\n"


@pytest.mark.parametrize("error, status, classification", [
    (subprocess.TimeoutExpired("mission", 20), "watchdog", "infrastructure"),
    (KeyboardInterrupt(), "interrupted", "interrupted"),
])
def test_wait_failures_map_to_result_statuses(tmp_path, monkeypatch, ros_ready, error, status,
                                              classification):
    # Arrange
    worker = _use_worker(monkeypatch, FakeWorker(_mission(), wait_error=error))

    # Act
    result = run_gazebo(Scenario(), tmp_path / "run", timeout=9, watchdog=20)

    # Assert
    assert (result.status, result.classification) == (status, classification)
    assert ros_ready == [worker]
    assert json.loads((tmp_path / "run" / "result.json").read_text())["status"] == status


def _timed_scenario() -> Scenario:
    return Scenario(
        version="2",
        objects=[ScenarioObject(id="chair", shape="box", pose=Pose(x=2.0, y=2.0),
                                size=Size(x=0.4, y=0.4, z=0.6))],
        events=[TimedMove(at=1.5, object_id="chair", pose=Pose(x=2.0, y=1.2))],
    )


def _logging_mission(log_line: str):
    base = _mission()

    def behaviour(directory: Path, logs) -> None:
        base(directory, logs)
        logs.write(log_line)
    return behaviour


def test_applied_timeline_events_are_recorded(tmp_path, monkeypatch, ros_ready):
    # Arrange
    _use_worker(monkeypatch, FakeWorker(_logging_mission("ENVFUZZ_EVENT 1 1500000000\n")))

    # Act
    result = run_gazebo(_timed_scenario(), tmp_path / "run", timeout=9, watchdog=20)

    # Assert
    assert result.status == "completed"
    records = json.loads((tmp_path / "run" / "events-applied.json").read_text())
    assert records == [{"object_id": "chair", "requested_sim_time": 1.5, "applied_sim_time": 1.5}]


def test_timeline_plugin_errors_invalidate_the_run(tmp_path, monkeypatch, ros_ready):
    # Arrange
    _use_worker(monkeypatch, FakeWorker(_logging_mission("ENVFUZZ_EVENT_ERROR chair\n")))

    # Act
    result = run_gazebo(_timed_scenario(), tmp_path / "run", timeout=9, watchdog=20)

    # Assert
    assert result.status == "simulator_failed"
    assert result.reason.startswith("Invalid mission result: Gazebo could not apply")


def test_profile_settings_reach_the_worker_environment(tmp_path, monkeypatch, ros_ready):
    # Arrange
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "lidar_hz: 10\nlidar_rays: 720\nlidar_fov_deg: 270\nlidar_noise_m: 0.01\n"
        "record_contacts: true\nrecord_motion: false\nnavigation_backend: amcl\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENVFUZZ_RECORD_CLIFF", "1")  # stale host setting must not leak through
    monkeypatch.setenv("ENVFUZZ_WORKER_DOMAIN", "42")
    worker = _use_worker(monkeypatch, FakeWorker(_mission()))
    output = tmp_path / "run"

    # Act
    run_gazebo(Scenario(), output, timeout=9, watchdog=20, profile_file=profile)

    # Assert
    env = worker.env
    assert (env["ENVFUZZ_LIDAR_HZ"], env["ENVFUZZ_LIDAR_RAYS"]) == ("10.0", "720")
    assert (env["ENVFUZZ_LIDAR_FOV_DEG"], env["ENVFUZZ_LIDAR_NOISE_M"]) == ("270.0", "0.01")
    assert env["ENVFUZZ_RECORD_CONTACTS"] == "1"
    assert "ENVFUZZ_RECORD_CLIFF" not in env and "ENVFUZZ_RECORD_MOTION" not in env
    assert env["ENVFUZZ_NAVIGATION_BACKEND"] == "amcl"
    assert env["ENVFUZZ_LOCALIZATION_MAP"] == str(output / "localization-map.yaml")
    assert (output / "localization-map.pgm").read_bytes().startswith(b"P5\n")
    assert env["ROS_DOMAIN_ID"] == "42"
    assert env["ROS_AUTOMATIC_DISCOVERY_RANGE"] == "LOCALHOST"
    run_config = json.loads((output / "run.json").read_text())
    assert run_config["ros_domain_id"] == "42"
    assert run_config["gazebo_partition"] == env["GZ_PARTITION"]
    assert (output / "profile.yaml").is_file()


def test_camera_profile_exports_camera_settings(tmp_path, monkeypatch, ros_ready):
    # Arrange
    profile = tmp_path / "camera.yaml"
    profile.write_text("camera_profile: true\nnavigation_backend: rtabmap\ncamera_hz: 10\n",
                       encoding="utf-8")
    monkeypatch.setattr(gazebo, "require_camera_capability", lambda: None)
    worker = _use_worker(monkeypatch, FakeWorker(_mission()))

    # Act
    run_gazebo(Scenario(), tmp_path / "run", timeout=9, watchdog=20, profile_file=profile)

    # Assert
    env = worker.env
    assert env["ENVFUZZ_CAMERA_PROFILE"] == "1"
    assert (env["ENVFUZZ_CAMERA_HZ"], env["ENVFUZZ_CAMERA_WIDTH"], env["ENVFUZZ_CAMERA_HEIGHT"]) == (
        "10.0", "320", "240")
    assert "ENVFUZZ_LOCALIZATION_MAP" not in env


def test_actor_timeline_records_interpolated_poses(tmp_path, monkeypatch, ros_ready):
    # Arrange
    scenario = Scenario.model_validate({
        "version": "2", "goals": [{"x": 4, "y": 0}],
        "objects": [{"id": "walker", "shape": "cylinder", "pose": {"x": 2, "y": 1.5},
                     "radius": 0.2, "height": 1.5}],
        "actors": [{"object_id": "walker", "keyframes": [{"at": 0.2, "pose": {"x": 2, "y": 1.7}}]}],
    })
    _use_worker(monkeypatch, FakeWorker(_logging_mission("ENVFUZZ_EVENT 1 100000000\n")))

    # Act
    result = run_gazebo(scenario, tmp_path / "run", timeout=9, watchdog=20)

    # Assert
    assert result.status == "completed"
    records = json.loads((tmp_path / "run" / "events-applied.json").read_text())
    assert [record["applied_sim_time"] for record in records] == [0.1, None]
    assert records[0]["pose"]["y"] == pytest.approx(1.6)
    assert records[1]["pose"] == {"x": 2.0, "y": 1.7, "yaw": 0.0}
