"""A small process-tree runner with explicit termination and cleanup semantics."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Sequence

from .results import EpisodeResult
from .scenario import Scenario, compile_sdf, validate_scenario


class EpisodeRunner:
    def __init__(self, watchdog_seconds: float = 300.0):
        self.watchdog_seconds = watchdog_seconds

    def run(
        self,
        scenario: Scenario,
        command: Sequence[str] | None = None,
        output: str | Path = "runs/episode",
        sim_timeout: float = 120.0,
        sim_time_fn=None,
    ) -> EpisodeResult:
        violations = validate_scenario(scenario)
        if violations:
            raise ValueError("; ".join(violations))
        start = time.monotonic()
        if command is None:
            result = EpisodeResult(status="completed", classification="behavior", sim_duration=0.0)
            _write_bundle(output, scenario, result)
            return result
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                list(command), start_new_session=True, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            while process.poll() is None:
                wall_elapsed = time.monotonic() - start
                sim_elapsed = sim_time_fn() if sim_time_fn is not None else wall_elapsed
                if wall_elapsed >= self.watchdog_seconds:
                    _terminate(process)
                    result = EpisodeResult(status="watchdog", classification="infrastructure",
                                           reason="wall-clock watchdog expired", wall_duration=wall_elapsed)
                    _write_bundle(output, scenario, result, process.stdout.read() if process.stdout else "")
                    return result
                if sim_elapsed >= sim_timeout:
                    _terminate(process)
                    result = EpisodeResult(status="timeout", classification="behavior",
                                           reason="simulation timeout", sim_duration=sim_elapsed,
                                           wall_duration=wall_elapsed)
                    _write_bundle(output, scenario, result, process.stdout.read() if process.stdout else "")
                    return result
                time.sleep(0.01)
            log = process.stdout.read() if process.stdout else ""
            status = "completed" if process.returncode == 0 else "simulator_failed"
            classification = "behavior" if process.returncode == 0 else "infrastructure"
            result = EpisodeResult(status=status, classification=classification,
                                   reason=None if process.returncode == 0 else f"exit code {process.returncode}",
                                   wall_duration=time.monotonic() - start)
            _write_bundle(output, scenario, result, log)
            return result
        except FileNotFoundError as error:
            result = EpisodeResult(status="launch_failed", classification="infrastructure",
                                   reason=str(error), wall_duration=time.monotonic() - start)
            _write_bundle(output, scenario, result)
            return result
        except KeyboardInterrupt:
            if process is not None:
                _terminate(process)
            result = EpisodeResult(status="interrupted", classification="interrupted",
                                   reason="user interrupted", wall_duration=time.monotonic() - start)
            _write_bundle(output, scenario, result)
            return result
        finally:
            if process is not None and process.poll() is None:
                _terminate(process)


def _terminate(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # A launch process may exit before every descendant has handled SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _write_bundle(output: str | Path, scenario: Scenario, result: EpisodeResult, logs: str = "") -> None:
    from .results import write_result_bundle
    write_result_bundle(output, scenario, compile_sdf(scenario), result, logs)
