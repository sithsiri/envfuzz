"""Measure a running headless stack in its own Docker container."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

from envfuzz.scenario import compile_sdf, load_scenario


def cpu_seconds() -> float:
    stats = dict(line.split() for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines())
    return int(stats["usage_usec"]) / 1e6


def measure(launch: subprocess.Popen) -> dict:
    rclpy.init(args=[])
    node = rclpy.create_node("envfuzz_lidar_probe")
    scans = []

    def receive(message: LaserScan) -> None:
        stamp = message.header.stamp
        scans.append((time.monotonic(), stamp.sec + stamp.nanosec / 1e9, len(message.ranges)))

    try:
        node.create_subscription(LaserScan, "/scan", receive, qos_profile_sensor_data)
        deadline = time.monotonic() + 45
        while not scans and time.monotonic() < deadline and launch.poll() is None:
            rclpy.spin_once(node, timeout_sec=0.2)
        if not scans:
            raise RuntimeError("No /scan messages. Check the launch logs.")
        scans.clear()
        cpu_start = cpu_seconds()
        wall_start = time.monotonic()
        while time.monotonic() - wall_start < 20:
            rclpy.spin_once(node, timeout_sec=0.2)
        wall_elapsed = time.monotonic() - wall_start
        cores = (cpu_seconds() - cpu_start) / wall_elapsed
        if len(scans) < 2 or scans[-1][1] <= scans[0][1]:
            raise RuntimeError("The measurement needs two scans with advancing simulation timestamps")
        wall_span = scans[-1][0] - scans[0][0]
        sim_span = scans[-1][1] - scans[0][1]
        return {
            "scans": len(scans),
            "rays_per_scan": sorted({sample[2] for sample in scans}),
            "wall_seconds": wall_elapsed,
            "scans_per_sim_second": (len(scans) - 1) / sim_span,
            "scans_per_wall_second": (len(scans) - 1) / wall_span,
            "real_time_factor": sim_span / wall_span,
            "container_cpu_cores": cores,
            "topics": sorted(name for name, _ in node.get_topic_names_and_types()),
        }
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main() -> None:
    os.environ.pop("DISPLAY", None)
    os.environ.update(
        LIBGL_ALWAYS_SOFTWARE="1", ROS_DOMAIN_ID="104",
        ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST", ROS_STATIC_PEERS="",
    )
    with tempfile.TemporaryDirectory() as directory:
        world = Path(directory) / "world.sdf"
        world.write_text(compile_sdf(load_scenario("examples/scenario.yaml")), encoding="utf-8")
        with tempfile.TemporaryFile() as logs:
            launch = subprocess.Popen(
                ["ros2", "launch", "envfuzz_sim", "headless.launch.py", f"world:={world}"],
                stdout=logs, stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                print(json.dumps(measure(launch), indent=2))
            except Exception:
                logs.seek(0)
                print(logs.read().decode(errors="replace"))
                raise
            finally:
                if launch.poll() is None:
                    launch.send_signal(signal.SIGINT)
                    try:
                        launch.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        os.killpg(launch.pid, signal.SIGKILL)
                        launch.wait()


if __name__ == "__main__":
    main()
