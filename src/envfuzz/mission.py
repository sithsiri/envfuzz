"""ROS-side mission driver. This module runs in the simulator's ROS domain."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import yaml

from action_msgs.msg import GoalStatus
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseWithCovarianceStamped
from lifecycle_msgs.msg import State
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid, Odometry
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import Imu, LaserScan
from tf2_ros import Buffer, TransformException, TransformListener

CONTACT_GAP_S = 0.1
CLIFF_RANGE_M = 0.20


@dataclass(frozen=True)
class PoseSample:
    sim_time: float
    x: float
    y: float
    yaw: float


def yaw(rotation) -> float:
    return math.atan2(
        2 * (rotation.w * rotation.z + rotation.x * rotation.y),
        1 - 2 * (rotation.y**2 + rotation.z**2),
    )


class Mission(Node):
    def __init__(
        self, output: Path, record_scans: bool = False, record_contacts: bool = False,
        record_cliff: bool = False, record_motion: bool = False, map_localization: bool = False,
    ):
        super().__init__("envfuzz_mission", parameter_overrides=[Parameter("use_sim_time", value=True)])
        self.client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self.lifecycle = self.create_client(GetState, "/bt_navigator/get_state")
        self.transforms = Buffer()
        self.listener = TransformListener(self.transforms, self)
        self.create_subscription(Odometry, "/envfuzz/ground_truth", self.record, qos_profile_sensor_data)
        self.create_subscription(OccupancyGrid, "/map", self.record_map,
                                 QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.output = output
        self.origin: PoseSample | None = None
        self.previous: PoseSample | None = None
        self.started: float | None = None
        self.distance = 0.0
        self.samples = 0
        self.paired = 0
        self.squared_pose_error = 0.0
        self.pending = deque()
        self.map_origin: tuple[float, float, float] | None = None
        self.world_origin: PoseSample | None = None
        self.latest_map: OccupancyGrid | None = None
        self.recoveries = []
        self.goal_recoveries = 0
        self.map_localization = map_localization
        self.last_initial_pose_time: float | None = None
        self.initial_pose = (
            self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 1)
            if map_localization else None
        )
        self.telemetry = (output / "telemetry.jsonl").open("w", encoding="utf-8", buffering=1)
        self.scans = None
        if record_scans:
            self.scans = (output / "scans.jsonl").open("w", encoding="utf-8", buffering=1)
            self.create_subscription(LaserScan, "/scan", self.record_scan, qos_profile_sensor_data)

        self.contacts = None
        self.contact_seen: dict[tuple[int, int], float] = {}
        if record_contacts:
            from ros_gz_interfaces.msg import Contacts

            self.contacts = (output / "contacts.jsonl").open("w", encoding="utf-8", buffering=1)
            self.create_subscription(Contacts, "/envfuzz/bumper", self.record_contact, qos_profile_sensor_data)

        self.cliff = None
        self.cliff_detected = False
        if record_cliff:
            self.cliff = (output / "cliff.jsonl").open("w", encoding="utf-8", buffering=1)
            self.create_subscription(LaserScan, "/envfuzz/cliff", self.record_cliff, qos_profile_sensor_data)
        self.motion = None
        if record_motion:
            self.motion = (output / "motion.jsonl").open("w", encoding="utf-8", buffering=1)
            self.create_subscription(Imu, "/imu", self.record_imu, qos_profile_sensor_data)
            self.create_subscription(Odometry, "/odom", self.record_wheel_odom, qos_profile_sensor_data)

    def record_cliff(self, message: LaserScan) -> None:
        distance = min((value for value in message.ranges if math.isfinite(value)), default=None)
        detected = distance is None or distance >= CLIFF_RANGE_M
        if detected != self.cliff_detected:
            self.cliff_detected = detected
            self.cliff.write(json.dumps({
                "kind": "cliff",
                "sim_time": Time.from_msg(message.header.stamp).nanoseconds / 1e9,
                "detected": detected,
                "range_m": distance,
            }) + "\n")

    def record_imu(self, message: Imu) -> None:
        acceleration = message.linear_acceleration
        angular = message.angular_velocity
        self.motion.write(json.dumps({
            "kind": "imu",
            "sim_time": Time.from_msg(message.header.stamp).nanoseconds / 1e9,
            "acceleration": [acceleration.x, acceleration.y, acceleration.z],
            "angular_velocity": [angular.x, angular.y, angular.z],
        }) + "\n")

    def record_wheel_odom(self, message: Odometry) -> None:
        pose = message.pose.pose
        self.motion.write(json.dumps({
            "kind": "wheel_odometry",
            "sim_time": Time.from_msg(message.header.stamp).nanoseconds / 1e9,
            "x": pose.position.x,
            "y": pose.position.y,
            "yaw": yaw(pose.orientation),
        }) + "\n")

    def record_contact(self, message) -> None:
        stamp = Time.from_msg(message.header.stamp).nanoseconds / 1e9
        for contact in message.contacts:
            left, right = sorted((contact.collision1, contact.collision2), key=lambda entity: entity.id)
            pair = (left.id, right.id)
            previous = self.contact_seen.get(pair)
            self.contact_seen[pair] = stamp
            if previous is not None and stamp - previous <= CONTACT_GAP_S:
                continue
            self.contacts.write(json.dumps({
                "kind": "bumper_contact",
                "sim_time": stamp,
                "collisions": [{"id": entity.id, "name": entity.name} for entity in (left, right)],
            }) + "\n")

    def record(self, message: Odometry) -> None:
        pose = message.pose.pose
        stamp = Time.from_msg(message.header.stamp)
        sample = PoseSample(sim_time=stamp.nanoseconds / 1e9,
                            x=pose.position.x, y=pose.position.y, yaw=yaw(pose.orientation))
        self.origin = sample
        if self.started is None or sample.sim_time < self.started:
            return
        if self.previous is not None:
            if sample.sim_time <= self.previous.sim_time:
                return
            self.distance += math.hypot(sample.x - self.previous.x, sample.y - self.previous.y)
        self.previous = sample
        self.samples += 1
        self.pending.append((sample, stamp))
        self.flush_telemetry()

    def flush_telemetry(self, final: bool = False) -> None:
        while self.pending:
            sample, stamp = self.pending[0]
            estimate = self.estimate(stamp)
            if estimate is None and not final and self.now() - sample.sim_time < 0.5:
                break
            self.pending.popleft()
            if estimate is not None:
                self.paired += 1
                self.squared_pose_error += (sample.x - estimate.x)**2 + (sample.y - estimate.y)**2
            row = {"ground_truth": asdict(sample),
                   "estimated": asdict(estimate) if estimate is not None else None}
            self.telemetry.write(json.dumps(row) + "\n")

    def estimate(self, stamp: Time) -> PoseSample | None:
        try:
            estimate = self.transforms.lookup_transform("map", "base_footprint", stamp).transform
        except TransformException:
            return None
        map_x, map_y, offset = self.map_origin
        anchor = self.world_origin
        dx, dy = estimate.translation.x - map_x, estimate.translation.y - map_y
        c, s = math.cos(offset), math.sin(offset)
        return PoseSample(sim_time=stamp.nanoseconds / 1e9,
                          x=anchor.x + dx * c + dy * s, y=anchor.y - dx * s + dy * c,
                          yaw=yaw(estimate.rotation) - offset)

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def record_map(self, message: OccupancyGrid) -> None:
        self.latest_map = message

    def record_scan(self, message: LaserScan) -> None:
        stamp = Time.from_msg(message.header.stamp)
        self.scans.write(json.dumps({
            "sim_time": stamp.nanoseconds / 1e9,
            "angle_min": message.angle_min,
            "angle_increment": message.angle_increment,
            "ranges": [value if math.isfinite(value) else None for value in message.ranges],
        }) + "\n")

    def record_feedback(self, message) -> None:
        count = message.feedback.number_of_recoveries
        if count > self.goal_recoveries:
            self.recoveries.append({"sim_time": self.now(), "count": count})
            self.goal_recoveries = count

    def publish_initial_pose(self) -> None:
        if self.initial_pose is None or self.origin is None:
            return
        now = self.now()
        if self.last_initial_pose_time is not None and now - self.last_initial_pose_time < 0.5:
            return
        message = PoseWithCovarianceStamped()
        message.header.frame_id = "map"
        message.header.stamp = self.get_clock().now().to_msg()
        message.pose.pose.position.x = self.origin.x
        message.pose.pose.position.y = self.origin.y
        message.pose.pose.orientation.z = math.sin(self.origin.yaw / 2)
        message.pose.pose.orientation.w = math.cos(self.origin.yaw / 2)
        message.pose.covariance[0] = message.pose.covariance[7] = 0.25
        message.pose.covariance[35] = 0.0685
        self.initial_pose.publish(message)
        self.last_initial_pose_time = now

    def save_map(self) -> None:
        if self.latest_map is None:
            return
        message = self.latest_map
        info = message.info
        origin = info.origin
        map_x, map_y, offset = self.map_origin
        anchor = self.world_origin
        dx, dy = origin.position.x - map_x, origin.position.y - map_y
        c, s = math.cos(offset), math.sin(offset)
        grid = {
            "width": info.width, "height": info.height, "resolution": info.resolution,
            "origin": {"x": anchor.x + dx * c + dy * s, "y": anchor.y - dx * s + dy * c,
                       "yaw": yaw(origin.orientation) - offset},
            "data": [-1 if value < 0 else 100 if value >= 50 else 0 for value in message.data],
        }
        if len(grid["data"]) <= 1_000_000:
            (self.output / "slam-map.json").write_text(json.dumps(grid), encoding="utf-8")
            (self.output / "map-alignment.json").write_text(json.dumps({
                "map_anchor": {"x": map_x, "y": map_y, "yaw_offset": offset},
                "world_anchor": asdict(anchor), "source_frame": message.header.frame_id,
            }, indent=2), encoding="utf-8")

    def run(self, goals: list[dict], launch: subprocess.Popen, timeout: float) -> dict:
        state = None
        while True:
            self.spin(launch)
            if not (self.now() > 0 and self.origin is not None and self.client.server_is_ready()):
                continue
            self.publish_initial_pose()
            try:
                initial = self.transforms.lookup_transform("map", "base_footprint", Time()).transform
            except TransformException:
                continue
            if state is None and self.lifecycle.service_is_ready():
                state = self.lifecycle.call_async(GetState.Request())
            elif state is not None and state.done():
                if state.result().current_state.id == State.PRIMARY_STATE_ACTIVE:
                    break
                state = None
        self.started = self.now()
        mission_wall_start = time.monotonic()
        anchor = self.origin
        offset = yaw(initial.rotation) - anchor.yaw
        self.map_origin = (initial.translation.x, initial.translation.y, offset)
        self.world_origin = anchor
        c, s = math.cos(offset), math.sin(offset)

        def finish(status, reason=None):
            self.flush_telemetry(final=True)
            self.save_map()
            duration = max(0.0, self.now() - self.started)
            wall_duration = time.monotonic() - mission_wall_start
            (self.output / "metrics.json").write_text(json.dumps({
                "goals_reached": reached,
                "goal_count": len(goals),
                "path_length_m": self.distance,
                "ground_truth_samples": self.samples,
                "paired_pose_samples": self.paired,
                "recoveries": self.recoveries,
                "mission_wall_duration_s": wall_duration,
                "mission_real_time_factor": duration / wall_duration if wall_duration > 0 else None,
                "position_rmse_m": math.sqrt(self.squared_pose_error / self.paired) if self.paired else None,
            }, indent=2) + "\n", encoding="utf-8")
            return {"status": status, "classification": "behavior",
                    "reason": reason, "sim_duration": duration}

        reached = 0
        for goal in goals:
            request = NavigateToPose.Goal()
            request.pose.header.frame_id = "map"
            request.pose.header.stamp = self.get_clock().now().to_msg()
            dx, dy = goal["x"] - anchor.x, goal["y"] - anchor.y
            request.pose.pose.position.x = initial.translation.x + dx * c - dy * s
            request.pose.pose.position.y = initial.translation.y + dx * s + dy * c
            request.pose.pose.orientation.z = math.sin((goal["yaw"] + offset) / 2)
            request.pose.pose.orientation.w = math.cos((goal["yaw"] + offset) / 2)
            self.goal_recoveries = 0
            accepted = self.client.send_goal_async(request, feedback_callback=self.record_feedback)
            if not self.wait(accepted, launch, timeout):
                return finish("timeout", "Simulation-time mission limit expired")
            handle = accepted.result()
            if not handle.accepted:
                raise RuntimeError("Nav2 rejected the navigation goal")
            outcome = handle.get_result_async()
            if not self.wait(outcome, launch, timeout):
                handle.cancel_goal_async()
                return finish("timeout", "Simulation-time mission limit expired")
            response = outcome.result()
            if response.status != GoalStatus.STATUS_SUCCEEDED:
                return finish("mission_failed", f"Nav2 reported action status {response.status}")
            reached += 1
        return finish("completed")

    def spin(self, launch: subprocess.Popen) -> None:
        if launch.poll() is not None:
            raise RuntimeError(f"ROS launch exited with code {launch.returncode}")
        rclpy.spin_once(self, timeout_sec=0.1)

    def wait(self, future, launch: subprocess.Popen, timeout: float) -> bool:
        while not future.done():
            if self.now() - self.started >= timeout:
                return False
            self.spin(launch)
        return True


def main() -> None:
    output = Path(sys.argv[1])
    timeout = float(sys.argv[2])
    scenario = json.loads((output / "scenario.json").read_text(encoding="utf-8"))
    start = scenario["start"]
    command = [
        "ros2", "launch", "envfuzz_sim", "headless.launch.py",
        f"world:={output / 'world.sdf'}",
        f"x_pose:={start['x']}", f"y_pose:={start['y']}", f"yaw:={start['yaw']}",
    ]
    params = output / "nav2.yaml"
    if not params.is_file():
        nav2 = Path(get_package_share_directory("nav2_bringup"))
        shutil.copyfile(nav2 / "params" / "nav2_params.yaml", params)
    if os.environ.get("ENVFUZZ_KEEPOUT_FILTER_MASK"):
        nav2_params = yaml.safe_load(params.read_text(encoding="utf-8"))
        for costmap in ("local_costmap", "global_costmap"):
            settings = nav2_params[costmap][costmap]["ros__parameters"]
            settings["filters"] = [
                *settings.get("filters", []), "keepout_filter", "keepout_inflation_layer",
            ]
            settings["keepout_filter"] = {
                "plugin": "nav2_costmap_2d::KeepoutFilter",
                "enabled": True,
                "filter_info_topic": "/costmap_filter_info",
                "transform_tolerance": 0.1,
            }
            settings["keepout_inflation_layer"] = {
                "plugin": "nav2_costmap_2d::InflationLayer",
                "enabled": True,
                "inflation_radius": 0.5,
                "cost_scaling_factor": 3.0,
            }
        params.write_text(yaml.safe_dump(nav2_params, sort_keys=False), encoding="utf-8")
    command.append(f"params_file:={params}")
    localization_map = os.environ.get("ENVFUZZ_LOCALIZATION_MAP")
    if localization_map:
        command.append(f"map:={localization_map}")
    rclpy.init(args=[])
    mission = Mission(
        output, record_scans=bool(scenario.get("events") or scenario.get("actors")),
        record_contacts=os.environ.get("ENVFUZZ_RECORD_CONTACTS") == "1",
        record_cliff=os.environ.get("ENVFUZZ_RECORD_CLIFF") == "1",
        record_motion=os.environ.get("ENVFUZZ_RECORD_MOTION") == "1",
        map_localization=os.environ.get("ENVFUZZ_NAVIGATION_BACKEND") == "amcl",
    )
    try:
        with subprocess.Popen(command) as launch:
            try:
                result = mission.run(scenario["goals"], launch, timeout)
            finally:
                if launch.poll() is None:
                    launch.send_signal(signal.SIGINT)
                    launch.wait()
        (output / "mission.json").write_text(json.dumps(result), encoding="utf-8")
    finally:
        mission.telemetry.close()
        if mission.scans is not None:
            mission.scans.close()
        for stream in (mission.contacts, mission.cliff, mission.motion):
            if stream is not None:
                stream.close()
        mission.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
