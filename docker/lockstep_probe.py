"""Measure a paused Gazebo world's step service in isolation."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from envfuzz.runner import _terminate
from envfuzz.scenario import Scenario, compile_sdf


def command(*arguments: str) -> str:
    return subprocess.check_output(["gz", *arguments], text=True, timeout=12)


def iterations(text: str) -> int:
    match = re.search(r"iterations: (\d+)", text)
    return int(match[1]) if match else 0


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        world = Path(directory) / "world.sdf"
        world.write_text(compile_sdf(Scenario()), encoding="utf-8")
        with subprocess.Popen(
            ["gz", "sim", "-s", "--headless-rendering", str(world)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        ) as server:
            try:
                deadline = time.monotonic() + 20
                while "/world/envfuzz/control" not in command("service", "-l"):
                    if time.monotonic() > deadline or server.poll() is not None:
                        raise RuntimeError("Paused world did not expose the control service")
                before = command("topic", "-e", "-t", "/world/envfuzz/stats", "-n", "1")
                start = time.monotonic()
                reply = command("service", "-s", "/world/envfuzz/control", "--reqtype", "gz.msgs.WorldControl",
                                "--reptype", "gz.msgs.Boolean", "--timeout", "5000",
                                "--req", "pause: true, multi_step: 20")
                after = command("topic", "-e", "-t", "/world/envfuzz/stats", "-n", "1")
                result = {"step_reply": reply.strip(), "iterations_before": iterations(before),
                          "iterations_after": iterations(after), "wall_seconds": time.monotonic() - start,
                          "paused_after": "paused: true" in after}
                print(json.dumps(result, indent=2))
                assert result["iterations_after"] - result["iterations_before"] == 20
                assert result["paused_after"]
            finally:
                _terminate(server)


if __name__ == "__main__":
    main()
