"""Simulator-free checks for the CPU lidar-only RTAB-Map backend (`rtabmap_lidar`)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from envfuzz import gazebo
from envfuzz.classroom import check_submission
from envfuzz.gazebo import run_gazebo
from envfuzz.profile import SimulationProfile, load_profile
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Scenario, save_scenario
from envfuzz.scoring import apply_baseline, mission_identity

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
LAUNCH_FILE = ROOT / "ros2_ws" / "src" / "envfuzz_sim" / "launch" / "headless.launch.py"
LIDAR_PROFILE = EXAMPLES / "rtabmap-lidar-profile.yaml"
BACKEND_ENVIRONMENT = ("ENVFUZZ_NAVIGATION_BACKEND", "ENVFUZZ_CAMERA_PROFILE", "ENVFUZZ_RECORD_CONTACTS",
                       "ENVFUZZ_RECORD_CLIFF", "ENVFUZZ_LOCALIZATION_MAP", "ENVFUZZ_WORKER_DOMAIN")


# --- profile validation -----------------------------------------------------------------------

def test_rtabmap_lidar_profile_resolves_without_a_camera():
    # Arrange / Act
    settings = load_profile(LIDAR_PROFILE).resolved()

    # Assert
    assert settings["navigation_backend"] == "rtabmap_lidar"
    assert settings["camera_profile"] is False
    assert settings["robot_model"] == "turtlebot3_waffle"
    assert SimulationProfile(navigation_backend="rtabmap_lidar").resolved()["navigation_backend"] == "rtabmap_lidar"


@pytest.mark.parametrize("settings, message", [
    ({"navigation_backend": "rtabmap_lidar", "camera_profile": True}, "lidar-only"),
    ({"navigation_backend": "rtabmap"}, "must be selected together"),
    ({"camera_profile": True}, "must be selected together"),
    ({"navigation_backend": "amcl", "camera_profile": True}, "must be selected together"),
])
def test_camera_profile_stays_tied_to_the_gpu_rtabmap_backend(settings, message):
    # Act / Assert
    with pytest.raises(ValueError, match=message):
        SimulationProfile(**settings)


def test_gpu_camera_profile_is_unchanged():
    # Act
    settings = load_profile(EXAMPLES / "rtabmap-camera-profile.yaml").resolved()

    # Assert
    assert (settings["navigation_backend"], settings["camera_profile"]) == ("rtabmap", True)


# --- runtime gate and worker environment -------------------------------------------------------

class _Worker:
    """Stands in for the mission subprocess and records the environment it receives."""

    pid = 999_999
    returncode = 0

    def __init__(self):
        self.env = None

    def __call__(self, command, env, stdout, stderr, start_new_session):
        self.env = env
        directory = Path(command[-2])
        (directory / "mission.json").write_text(json.dumps(
            {"status": "completed", "classification": "behavior", "sim_duration": 4.0}))
        (directory / "telemetry.jsonl").write_text(
            '{"ground_truth": {"sim_time": 1.0, "x": 0.0, "y": 0.0}, "estimated": null}\n')
        return self

    def wait(self, timeout):
        return self.returncode


@pytest.fixture
def worker(monkeypatch):
    for name in BACKEND_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    fake = _Worker()
    monkeypatch.setattr(gazebo, "shutil", SimpleNamespace(
        which=lambda name: "/opt/ros/bin/ros2" if name == "ros2" else None, copyfile=shutil.copyfile))
    monkeypatch.setattr(gazebo, "importlib", SimpleNamespace(util=SimpleNamespace(find_spec=lambda _: object())))
    monkeypatch.setattr(gazebo, "_terminate", lambda process: None)
    proxy = SimpleNamespace(**{name: getattr(subprocess, name) for name in (
        "PIPE", "DEVNULL", "STDOUT", "TimeoutExpired", "run")}, Popen=fake)
    monkeypatch.setattr(gazebo, "subprocess", proxy)
    return fake


@pytest.fixture
def gpu_checks(monkeypatch):
    calls = []

    def refuse():
        calls.append("nvidia-smi")
        raise OSError("The RTAB-Map camera profile requires an NVIDIA GPU exposed to the runtime")

    monkeypatch.setattr(gazebo, "require_camera_capability", refuse)
    return calls


def test_rtabmap_lidar_runs_without_the_gpu_gate(tmp_path, worker, gpu_checks):
    # Arrange
    output = tmp_path / "run"

    # Act
    result = run_gazebo(Scenario(), output, timeout=9, watchdog=20, profile_file=LIDAR_PROFILE)

    # Assert
    assert gpu_checks == []
    assert result.status == "completed"
    assert worker.env["ENVFUZZ_NAVIGATION_BACKEND"] == "rtabmap_lidar"
    assert "ENVFUZZ_CAMERA_PROFILE" not in worker.env
    assert "ENVFUZZ_LOCALIZATION_MAP" not in worker.env
    assert not (output / "localization-map.yaml").exists()
    resolved = json.loads((output / "run.json").read_text())["simulation_profile"]
    assert (resolved["navigation_backend"], resolved["camera_profile"]) == ("rtabmap_lidar", False)
    assert load_profile(output / "profile.yaml").resolved()["navigation_backend"] == "rtabmap_lidar"


def test_rtabmap_camera_profile_still_requires_the_gpu_gate(tmp_path, worker, gpu_checks):
    # Act / Assert
    with pytest.raises(OSError, match="requires an NVIDIA GPU"):
        run_gazebo(Scenario(), tmp_path / "run", timeout=9, watchdog=20,
                   profile_file=EXAMPLES / "rtabmap-camera-profile.yaml")
    assert gpu_checks == ["nvidia-smi"]
    assert worker.env is None
    assert not (tmp_path / "run").exists()


# --- matching baselines ------------------------------------------------------------------------

def _bundle(directory: Path, profile: Path | None) -> Path:
    directory.mkdir()
    (directory / "nav2.yaml").write_text("nav2: {}\n", encoding="utf-8")
    (directory / "telemetry.jsonl").write_text(
        '{"ground_truth": {"sim_time": 0, "x": 0, "y": 0}, "estimated": null}\n', encoding="utf-8")
    if profile is not None:
        shutil.copyfile(profile, directory / "profile.yaml")
    return directory


def test_each_backend_needs_its_own_baseline(tmp_path):
    # Arrange
    bundles = {
        "slam_toolbox": _bundle(tmp_path / "slam", None),
        "rtabmap_lidar": _bundle(tmp_path / "lidar", LIDAR_PROFILE),
        "amcl": _bundle(tmp_path / "amcl", EXAMPLES / "amcl-localization-profile.yaml"),
    }
    identities = {name: mission_identity(Scenario(), bundle / "nav2.yaml") for name, bundle in bundles.items()}
    lidar_baseline = {"schema_version": "1", "identity": identities["rtabmap_lidar"],
                      "thresholds": {"position_error_m": 0.1}}

    # Act
    accepted = EpisodeResult(status="completed", classification="behavior")
    apply_baseline(accepted, Scenario(), bundles["rtabmap_lidar"], lidar_baseline)

    # Assert
    assert len({json.dumps(identity, sort_keys=True) for identity in identities.values()}) == 3
    assert "localization_map_sha256" not in identities["rtabmap_lidar"]
    assert accepted.baseline_sha256 is not None
    for other in ("slam_toolbox", "amcl"):
        with pytest.raises(ValueError, match="do not match"):
            apply_baseline(EpisodeResult(status="completed", classification="behavior"),
                           Scenario(), bundles[other], lidar_baseline)


# --- classroom challenges ----------------------------------------------------------------------

def _challenge(directory: Path, backend: str, profile: Path) -> tuple[Path, Path]:
    directory.mkdir()
    save_scenario(Scenario(), directory / "scenario.yaml")
    shutil.copyfile(profile, directory / "profile.yaml")
    manifest = directory / "challenge.yaml"
    manifest.write_text(
        "id: lidar\ndifficulty: intro\nscenario: scenario.yaml\nbaseline: baseline.json\n"
        f"profile: profile.yaml\nnavigation_backend: {backend}\n", encoding="utf-8")
    submission = directory / "entry.json"
    submission.write_text('{"challenge":"lidar","team":"t","edits":[]}', encoding="utf-8")
    return manifest, submission


def test_classroom_challenge_accepts_a_matching_rtabmap_lidar_profile(tmp_path):
    # Arrange
    manifest, submission = _challenge(tmp_path / "ok", "rtabmap_lidar", LIDAR_PROFILE)
    mismatched = _challenge(tmp_path / "bad", "rtabmap_lidar", EXAMPLES / "amcl-localization-profile.yaml")

    # Act
    challenge, _, _, _ = check_submission(manifest, submission)

    # Assert
    assert challenge.navigation_backend == "rtabmap_lidar"
    with pytest.raises(ValueError, match="does not match its simulation profile"):
        check_submission(*mismatched)


# --- launch file branch selection ---------------------------------------------------------------

class _Action:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


class _Include(_Action):
    pass


class _Node(_Action):
    pass


class _Description:
    def __init__(self, entities):
        self.entities = list(entities)


def _fake_ros_modules(monkeypatch, share: Path) -> None:
    def module(name: str, **members) -> None:
        fake = ModuleType(name)
        fake.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, fake)

    module("xacro", process_file=None)
    module("ament_index_python")
    module("ament_index_python.packages", get_package_share_directory=lambda name: str(share / name),
           get_package_prefix=lambda name: str(share / "prefix" / name))
    module("launch", LaunchDescription=_Description)
    module("launch.actions", AppendEnvironmentVariable=_Action, DeclareLaunchArgument=_Action,
           ExecuteProcess=_Action, IncludeLaunchDescription=_Include)
    module("launch.launch_description_sources", PythonLaunchDescriptionSource=_Action)
    module("launch.substitutions", LaunchConfiguration=_Action)
    module("launch_ros")
    module("launch_ros.actions", Node=_Node)


@pytest.fixture
def launch_file(tmp_path, monkeypatch):
    urdf = tmp_path / "nav2_minimal_tb3_sim" / "urdf"
    urdf.mkdir(parents=True)
    (urdf / "turtlebot3_waffle.urdf").write_text("<robot/>", encoding="utf-8")
    _fake_ros_modules(monkeypatch, tmp_path)
    spec = importlib.util.spec_from_file_location("envfuzz_headless_launch", LAUNCH_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "robot_sdf", lambda *args, **kwargs: "<sdf/>")
    for name in BACKEND_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    return module


def _describe(module, monkeypatch, backend: str | None, camera: bool = False) -> dict:
    if backend is not None:
        monkeypatch.setenv("ENVFUZZ_NAVIGATION_BACKEND", backend)
    if camera:
        monkeypatch.setenv("ENVFUZZ_CAMERA_PROFILE", "1")
    entities = module.generate_launch_description().entities
    includes = {Path(entity.args[0].args[0]).name: dict(entity.kwargs.get("launch_arguments", {}))
                for entity in entities if isinstance(entity, _Include)}
    nodes = [entity.kwargs for entity in entities if isinstance(entity, _Node)]
    return {"bringup": includes["bringup_launch.py"], "includes": set(includes),
            "packages": [node["package"] for node in nodes], "nodes": nodes}


@pytest.mark.parametrize("backend, camera, bringup", [
    (None, False, {"slam": "True"}),
    ("slam_toolbox", False, {"slam": "True"}),
    ("amcl", False, {"slam": "False"}),
    ("rtabmap", True, {"slam": "False"}),
    ("rtabmap_lidar", False, {"slam": "False", "use_localization": "False"}),
])
def test_launch_selects_the_navigation_mode_for_each_backend(launch_file, monkeypatch, backend, camera, bringup):
    # Act
    description = _describe(launch_file, monkeypatch, backend, camera)

    # Assert
    selected = {key: description["bringup"][key] for key in ("slam", "use_localization")
                if key in description["bringup"]}
    assert selected == bringup
    assert ("rtabmap_slam" in description["packages"]) == (backend == "rtabmap_lidar")
    assert ("rtabmap.launch.py" in description["includes"]) == camera


def test_rtabmap_lidar_node_uses_scan_and_wheel_odometry_only(launch_file, monkeypatch):
    # Act
    description = _describe(launch_file, monkeypatch, "rtabmap_lidar")

    # Assert
    [node] = [node for node in description["nodes"] if node["package"] == "rtabmap_slam"]
    [parameters] = node["parameters"]
    assert node["executable"] == "rtabmap"
    assert parameters["use_sim_time"] is True
    assert (parameters["subscribe_scan"], parameters["subscribe_rgb"], parameters["subscribe_depth"]) == (
        True, False, False)
    assert parameters["frame_id"] == "base_footprint"
    assert (parameters["Reg/Strategy"], parameters["Reg/Force3DoF"]) == ("1", "true")
    assert dict(node["remappings"]) == {"scan": "/scan", "odom": "/odom"}
    assert "rtabmap_viz" not in description["packages"]
    assert not any("camera" in str(node.get("arguments", "")) for node in description["nodes"])
