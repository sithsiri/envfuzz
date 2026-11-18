"""Process-tree runner behavior with short-lived local Python children (no ROS or Gazebo)."""

import json
import signal
import subprocess
import sys

import pytest

from envfuzz import runner as runner_module
from envfuzz.results import EpisodeResult
from envfuzz.runner import EpisodeRunner, _terminate
from envfuzz.scenario import Pose, Scenario

LONG_SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


def _read_result(bundle) -> EpisodeResult:
    return EpisodeResult.model_validate_json((bundle / "result.json").read_text(encoding="utf-8"))


@pytest.fixture
def spawned(monkeypatch):
    """Record every child the runner launches so tests can check it was reaped."""
    original = subprocess.Popen
    processes = []

    def tracking_popen(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runner_module.subprocess, "Popen", tracking_popen)
    return processes


def test_invalid_scenario_is_rejected_before_any_bundle_is_written(tmp_path):
    scenario = Scenario(start=Pose(x=4.9, y=0.0))
    output = tmp_path / "bundle"

    with pytest.raises(ValueError, match="start: violates wall clearance"):
        EpisodeRunner().run(scenario, output=output)

    assert not output.exists()


def test_smoke_run_without_command_writes_completed_bundle(tmp_path):
    output = tmp_path / "bundle"

    result = EpisodeRunner().run(Scenario(), output=output)

    assert (result.status, result.classification, result.sim_duration) == ("completed", "behavior", 0.0)
    assert _read_result(output) == result
    assert json.loads((output / "scenario.json").read_text())["goals"] == [{"x": 2.0, "y": 0.0, "yaw": 0.0}]
    assert (output / "world.sdf").read_text().startswith("<?xml")


def test_successful_command_records_output_log_and_behavior_result(tmp_path):
    output = tmp_path / "bundle"
    command = [sys.executable, "-c", "print('launched ok')"]

    result = EpisodeRunner(watchdog_seconds=30).run(Scenario(), command=command, output=output)

    assert (result.status, result.classification, result.reason) == ("completed", "behavior", None)
    assert result.wall_duration > 0
    assert (output / "logs.txt").read_text() == "launched ok\n"


def test_nonzero_exit_is_classified_as_simulator_infrastructure_failure(tmp_path):
    output = tmp_path / "bundle"
    command = [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"]

    result = EpisodeRunner(watchdog_seconds=30).run(Scenario(), command=command, output=output)

    assert (result.status, result.classification, result.reason) == (
        "simulator_failed", "infrastructure", "exit code 3",
    )
    assert _read_result(output).status == "simulator_failed"
    assert (output / "logs.txt").read_text() == "boom\n"


def test_missing_executable_is_reported_as_launch_failure(tmp_path):
    output = tmp_path / "bundle"

    result = EpisodeRunner().run(Scenario(), command=[str(tmp_path / "no-such-binary")], output=output)

    assert (result.status, result.classification) == ("launch_failed", "infrastructure")
    assert "no-such-binary" in result.reason
    assert _read_result(output).status == "launch_failed"


def test_simulation_timeout_terminates_child_and_is_a_behavior_result(tmp_path, spawned):
    output = tmp_path / "bundle"

    result = EpisodeRunner(watchdog_seconds=30).run(
        Scenario(), command=LONG_SLEEP, output=output, sim_timeout=5.0, sim_time_fn=lambda: 7.5,
    )

    assert (result.status, result.classification, result.reason) == ("timeout", "behavior", "simulation timeout")
    assert result.sim_duration == 7.5
    assert spawned[0].returncode is not None
    assert _read_result(output).status == "timeout"


def test_wall_clock_watchdog_terminates_child_as_infrastructure(tmp_path, spawned):
    output = tmp_path / "bundle"

    result = EpisodeRunner(watchdog_seconds=0.0).run(Scenario(), command=LONG_SLEEP, output=output)

    assert (result.status, result.classification) == ("watchdog", "infrastructure")
    assert result.reason == "wall-clock watchdog expired"
    assert spawned[0].returncode is not None
    assert (output / "logs.txt").exists()


def test_wall_clock_is_used_as_sim_time_when_no_clock_function_is_given(tmp_path, spawned):
    output = tmp_path / "bundle"

    result = EpisodeRunner(watchdog_seconds=30).run(
        Scenario(), command=LONG_SLEEP, output=output, sim_timeout=0.05,
    )

    assert result.status == "timeout"
    assert result.sim_duration >= 0.05
    assert spawned[0].returncode is not None


def test_keyboard_interrupt_terminates_child_and_writes_interrupted_bundle(tmp_path, spawned):
    output = tmp_path / "bundle"

    def interrupt():
        raise KeyboardInterrupt

    result = EpisodeRunner(watchdog_seconds=30).run(
        Scenario(), command=LONG_SLEEP, output=output, sim_time_fn=interrupt,
    )

    assert (result.status, result.classification, result.reason) == (
        "interrupted", "interrupted", "user interrupted",
    )
    assert spawned[0].returncode is not None
    assert _read_result(output).status == "interrupted"


def test_unexpected_error_still_terminates_running_child(tmp_path, spawned):
    output = tmp_path / "bundle"

    def broken_clock():
        raise RuntimeError("clock bridge failed")

    with pytest.raises(RuntimeError, match="clock bridge failed"):
        EpisodeRunner(watchdog_seconds=30).run(
            Scenario(), command=LONG_SLEEP, output=output, sim_time_fn=broken_clock,
        )

    assert spawned[0].returncode is not None
    assert not output.exists()


class _FakeProcess:
    def __init__(self, timeout_first_wait: bool):
        self.pid = 4242
        self.waits = []
        self._timeout_first_wait = timeout_first_wait

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self._timeout_first_wait and len(self.waits) == 1:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


def test_terminate_escalates_to_sigkill_when_group_ignores_sigterm(monkeypatch):
    signals = []
    monkeypatch.setattr(runner_module.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    process = _FakeProcess(timeout_first_wait=True)

    _terminate(process)

    assert signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert process.waits == [2, None]


def test_terminate_reaps_process_whose_group_already_exited(monkeypatch):
    def missing_group(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(runner_module.os, "killpg", missing_group)
    process = _FakeProcess(timeout_first_wait=False)

    _terminate(process)

    assert process.waits == [None]


def test_terminate_tolerates_group_exiting_before_sigkill(monkeypatch):
    signals = []

    def killpg(pid, sig):
        signals.append(sig)
        if sig == signal.SIGKILL:
            raise ProcessLookupError

    monkeypatch.setattr(runner_module.os, "killpg", killpg)
    process = _FakeProcess(timeout_first_wait=False)

    _terminate(process)

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.waits == [2, None]
