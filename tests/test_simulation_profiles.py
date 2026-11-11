import json
from pathlib import Path

import pytest

from envfuzz.classroom import check_submission
from envfuzz.gazebo import require_camera_capability, run_gazebo
from envfuzz.profile import SimulationProfile, load_profile
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Pose, Scenario, ScenarioObject, Size, save_scenario
from envfuzz.scoring import apply_baseline, mission_identity

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_profiles_have_legacy_defaults_and_allowlisted_values():
    settings = SimulationProfile().resolved()
    assert settings["robot_model"] == "turtlebot3_waffle"
    assert settings["navigation_backend"] == "slam_toolbox"
    assert settings["lidar_fov_deg"] == 360
    with pytest.raises(ValueError):
        SimulationProfile(robot_model="arbitrary")
    assert load_profile(EXAMPLES / "compact-vacuum-profile.yaml").resolved()["lidar_fov_deg"] == 270


def test_rtabmap_camera_profile_is_explicit_and_gpu_gated(monkeypatch):
    camera = load_profile(EXAMPLES / "rtabmap-camera-profile.yaml")
    assert camera.resolved()["navigation_backend"] == "rtabmap"
    assert camera.resolved()["camera_profile"] is True
    with pytest.raises(ValueError, match="must be selected together"):
        SimulationProfile(navigation_backend="rtabmap")
    with pytest.raises(ValueError, match="must be selected together"):
        SimulationProfile(camera_profile=True)
    monkeypatch.setattr("envfuzz.gazebo.shutil.which", lambda _: None)
    with pytest.raises(OSError, match="requires an NVIDIA GPU"):
        require_camera_capability()


def test_challenge_matches_resolved_robot_profile(tmp_path):
    save_scenario(Scenario(), tmp_path / "scenario.yaml")
    (tmp_path / "profile.yaml").write_bytes((EXAMPLES / "compact-vacuum-profile.yaml").read_bytes())
    manifest = tmp_path / "challenge.yaml"
    manifest.write_text(
        "id: test\ndifficulty: intro\nscenario: scenario.yaml\nbaseline: baseline.json\n"
        "profile: profile.yaml\n",
        encoding="utf-8",
    )
    submission = tmp_path / "submission.json"
    submission.write_text('{"challenge":"test","team":"test","edits":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="does not match its simulation profile"):
        check_submission(manifest, submission)
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write("robot: compact_vacuum\n")
    challenge, _, _, _ = check_submission(manifest, submission)
    assert challenge.robot == "compact_vacuum"


def test_baseline_identity_rejects_a_different_robot_profile(tmp_path):
    scenario = Scenario()
    params = tmp_path / "nav2.yaml"
    params.write_text("nav2: {}")
    trace = tmp_path / "telemetry.jsonl"
    trace.write_text('{"ground_truth":{"sim_time":0,"x":0,"y":0},"estimated":null}\n')
    profile = tmp_path / "profile.yaml"
    profile.write_text("robot_model: compact_vacuum\n")
    baseline = {"schema_version": "1", "identity": mission_identity(scenario, params),
                "thresholds": {"position_error_m": 0.1}}
    result = EpisodeResult(status="completed", classification="behavior")
    apply_baseline(result, scenario, tmp_path, baseline)
    profile.write_text("robot_model: turtlebot3_waffle\n")
    with pytest.raises(ValueError, match="do not match"):
        apply_baseline(result, scenario, tmp_path, baseline)


def test_run_bundles_record_the_resolved_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("envfuzz.gazebo.shutil.which", lambda _: None)
    bundle = tmp_path / "run"
    run_gazebo(Scenario(), bundle, 20, 30, profile_file=EXAMPLES / "compact-vacuum-profile.yaml")
    run = json.loads((bundle / "run.json").read_text())
    assert run["simulation_profile"]["robot_model"] == "compact_vacuum"
    assert run["simulation_profile"]["record_contacts"]


def test_amcl_profile_archives_its_deterministic_map(tmp_path, monkeypatch):
    monkeypatch.setattr("envfuzz.gazebo.shutil.which", lambda _: None)
    bundle = tmp_path / "run"
    run_gazebo(Scenario(), bundle, 20, 30, profile_file=EXAMPLES / "amcl-localization-profile.yaml")
    assert (bundle / "localization-map.yaml").is_file()
    assert (bundle / "localization-map.pgm").is_file()
    assert json.loads((bundle / "run.json").read_text())["simulation_profile"]["navigation_backend"] == "amcl"


def test_amcl_baseline_identity_changes_when_the_exported_map_changes(tmp_path):
    params = tmp_path / "nav2.yaml"
    params.write_text("nav2: {}")
    (tmp_path / "profile.yaml").write_bytes((EXAMPLES / "amcl-localization-profile.yaml").read_bytes())
    first = mission_identity(Scenario(), params)
    changed = Scenario(objects=[ScenarioObject(
        id="wall", shape="box", pose=Pose(x=1, y=1), size=Size(x=.5, y=.5),
    )])
    assert mission_identity(changed, params)["localization_map_sha256"] != first["localization_map_sha256"]
