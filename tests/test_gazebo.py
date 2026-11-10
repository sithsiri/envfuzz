import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from envfuzz.cli import app
from envfuzz.gazebo import run_gazebo
from envfuzz.report import render_report
from envfuzz.scenario import Scenario, load_scenario


def test_missing_ros_writes_a_failure_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr("envfuzz.gazebo.shutil.which", lambda _: None)
    output = tmp_path / "run"
    result = run_gazebo(Scenario(), output, timeout=10, watchdog=20)
    assert result.status == "launch_failed"
    assert json.loads((output / "result.json").read_text())["classification"] == "infrastructure"
    assert (output / "world.sdf").is_file()


def test_cli_run_and_replay_save_results_and_reuse_config(tmp_path, monkeypatch):
    real_popen = subprocess.Popen
    calls = []

    def start_worker(command, **kwargs):
        directory = Path(command[-2])
        calls.append((directory, float(command[-1]), kwargs["env"]))
        return real_popen([sys.executable, "-c", """
import json
from pathlib import Path
import sys

directory = Path(sys.argv[1])
assert (directory / 'world.sdf').is_file()
assert (directory / 'nav2.yaml').read_text() == 'nav2: {}'
(directory / 'mission.json').write_text(json.dumps({
    'status': 'completed', 'classification': 'behavior', 'sim_duration': 2.5
}))
(directory / 'telemetry.jsonl').write_text(json.dumps({
    'ground_truth': {'sim_time': 1.0, 'x': 0.0, 'y': 0.0}, 'estimated': None
}))
print('worker log')
""", str(directory)], **kwargs)

    monkeypatch.setattr("envfuzz.gazebo.shutil.which", lambda name: "/opt/ros/ros2" if name == "ros2" else None)
    monkeypatch.setattr("envfuzz.gazebo.importlib.util.find_spec", lambda _: True)
    monkeypatch.setattr("envfuzz.gazebo.subprocess.Popen", start_worker)
    scenario = Path(__file__).resolve().parents[1] / "examples" / "scenario.yaml"
    params = tmp_path / "params.yaml"
    params.write_text("nav2: {}", encoding="utf-8")
    cli = CliRunner()
    original = tmp_path / "original"
    replay = tmp_path / "replay"
    run = cli.invoke(app, ["run", str(scenario), "--output", str(original), "--timeout", "12",
                           "--params-file", str(params)])
    assert run.exit_code == 0, run.output
    repeated = cli.invoke(app, ["replay", str(original), "--output", str(replay)])
    assert repeated.exit_code == 0, repeated.output
    assert [call[1] for call in calls] == [12, 12]
    assert calls[0][2]["GZ_PARTITION"] != calls[1][2]["GZ_PARTITION"]
    for bundle in (original, replay):
        assert json.loads((bundle / "result.json").read_text())["sim_duration"] == 2.5
        assert (bundle / "logs.txt").read_text() == "worker log\n"


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ENVFUZZ_GAZEBO_TEST") != "1", reason="requires ROS 2 and Gazebo")
def test_headless_example_mission(tmp_path):
    scenario = load_scenario(Path(__file__).resolve().parents[1] / "examples" / "scenario.yaml")
    output = tmp_path / "run"
    result = run_gazebo(scenario, output, timeout=40, watchdog=90)
    assert result.status == "completed", (output / "logs.txt").read_text()
    assert result.sim_duration > 0
    metrics = json.loads((output / "metrics.json").read_text())
    assert metrics["goals_reached"] == 1
    assert metrics["path_length_m"] > 1.0
    assert metrics["paired_pose_samples"] > 0
    render_report(output, output / "trajectory.svg")
    assert ET.parse(output / "trajectory.svg").getroot().tag.endswith("svg")
