"""Exercise the ROS mission driver through injected fake ROS modules (no ROS install needed)."""

from __future__ import annotations

import importlib
import json
import math
import signal
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest

import envfuzz

NS_PER_S = 1_000_000_000
SPIN_STEP_S = 0.1
ACTIVE_STATE_ID = 3
SUCCEEDED_STATUS = 4
ABORTED_STATUS = 6


class FakeTime:
    def __init__(self, nanoseconds: int = 0, seconds: float | None = None):
        self.nanoseconds = round(seconds * NS_PER_S) if seconds is not None else nanoseconds

    @classmethod
    def from_msg(cls, stamp) -> FakeTime:
        return cls(nanoseconds=stamp.nanoseconds)

    def to_msg(self) -> SimpleNamespace:
        return SimpleNamespace(nanoseconds=self.nanoseconds)


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def now(self) -> FakeTime:
        return FakeTime(seconds=self.seconds)


class FakeTransformError(Exception):
    pass


class FakeBuffer:
    """Return the configured map->base transform, or raise when none is configured."""

    def __init__(self) -> None:
        self.transform = None
        self.failures_before_success = 0

    def lookup_transform(self, target, source, stamp):
        assert (target, source) == ("map", "base_footprint")
        if self.failures_before_success > 0:
            self.failures_before_success -= 1
            raise FakeTransformError("not yet")
        if self.transform is None:
            raise FakeTransformError("no transform")
        return SimpleNamespace(transform=self.transform)


class FakePublisher:
    def __init__(self) -> None:
        self.messages = []

    def publish(self, message) -> None:
        self.messages.append(message)


class FakeFuture:
    def __init__(self, value=None, done: bool = True):
        self.value = value
        self.is_done = done

    def done(self) -> bool:
        return self.is_done

    def result(self):
        return self.value


class FakeLifecycleClient:
    def __init__(self, states: list[int]) -> None:
        self.states = list(states)
        self.calls = 0

    def service_is_ready(self) -> bool:
        return True

    def call_async(self, request) -> FakeFuture:
        state_id = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        return FakeFuture(SimpleNamespace(current_state=SimpleNamespace(id=state_id)))


class FakeGoalHandle:
    def __init__(self, accepted: bool, outcome: FakeFuture) -> None:
        self.accepted = accepted
        self.outcome = outcome
        self.cancelled = False

    def get_result_async(self) -> FakeFuture:
        return self.outcome

    def cancel_goal_async(self) -> None:
        self.cancelled = True


class FakeActionClient:
    """Answer each goal with the next scripted (accepted_future, handle) pair."""

    def __init__(self, node, action_type, name) -> None:
        self.name = name
        self.ready = True
        self.script: list[FakeFuture] = []
        self.requests = []

    def server_is_ready(self) -> bool:
        return self.ready

    def send_goal_async(self, request, feedback_callback=None) -> FakeFuture:
        self.requests.append((request, feedback_callback))
        return self.script.pop(0)


class FakeNode:
    def __init__(self, name, parameter_overrides=None) -> None:
        self.node_name = name
        self.parameter_overrides = parameter_overrides
        self.subscriptions = {}
        self.publishers = {}
        self.clients = {}
        self.clock = FakeClock()
        self.destroyed = False

    def create_client(self, service_type, name):
        client = FakeLifecycleClient([ACTIVE_STATE_ID])
        self.clients[name] = client
        return client

    def create_subscription(self, message_type, topic, callback, qos):
        self.subscriptions[topic] = callback

    def create_publisher(self, message_type, topic, depth):
        publisher = FakePublisher()
        self.publishers[topic] = publisher
        return publisher

    def get_clock(self) -> FakeClock:
        return self.clock

    def destroy_node(self) -> None:
        self.destroyed = True


def _nested_message() -> SimpleNamespace:
    pose = SimpleNamespace(
        position=SimpleNamespace(x=0.0, y=0.0, z=0.0),
        orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
    )
    return SimpleNamespace(
        header=SimpleNamespace(frame_id="", stamp=None),
        pose=SimpleNamespace(pose=pose, covariance=[0.0] * 36),
    )


def _install_fake_ros(monkeypatch) -> SimpleNamespace:
    """Register fake ROS packages in sys.modules; monkeypatch restores them afterwards."""
    spin_hooks = []

    def spin_once(node, timeout_sec):
        node.clock.seconds = round(node.clock.seconds + SPIN_STEP_S, 9)
        for hook in spin_hooks:
            hook(node)

    modules = {
        "action_msgs": {}, "action_msgs.msg": {
            "GoalStatus": SimpleNamespace(STATUS_SUCCEEDED=SUCCEEDED_STATUS)},
        "ament_index_python": {}, "ament_index_python.packages": {
            "get_package_share_directory": lambda name: f"/fake/share/{name}"},
        "geometry_msgs": {}, "geometry_msgs.msg": {"PoseWithCovarianceStamped": _nested_message},
        "lifecycle_msgs": {}, "lifecycle_msgs.msg": {
            "State": SimpleNamespace(PRIMARY_STATE_ACTIVE=ACTIVE_STATE_ID)},
        "lifecycle_msgs.srv": {"GetState": SimpleNamespace(Request=lambda: "get-state")},
        "nav2_msgs": {}, "nav2_msgs.action": {
            "NavigateToPose": SimpleNamespace(Goal=lambda: SimpleNamespace(pose=_goal_pose()))},
        "nav_msgs": {}, "nav_msgs.msg": {"OccupancyGrid": object, "Odometry": object},
        "rclpy": {"init": lambda args=None: None, "spin_once": spin_once,
                  "try_shutdown": lambda: None},
        "rclpy.action": {"ActionClient": FakeActionClient},
        "rclpy.node": {"Node": FakeNode},
        "rclpy.parameter": {"Parameter": lambda name, value=None: (name, value)},
        "rclpy.qos": {"DurabilityPolicy": SimpleNamespace(TRANSIENT_LOCAL="transient_local"),
                      "QoSProfile": lambda depth, durability: (depth, durability),
                      "qos_profile_sensor_data": "sensor"},
        "rclpy.time": {"Time": FakeTime},
        "sensor_msgs": {}, "sensor_msgs.msg": {"Imu": object, "LaserScan": object},
        "tf2_ros": {"Buffer": FakeBuffer, "TransformException": FakeTransformError,
                    "TransformListener": lambda buffer, node: None},
        "ros_gz_interfaces": {}, "ros_gz_interfaces.msg": {"Contacts": object},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "envfuzz.mission", raising=False)
    monkeypatch.setattr(envfuzz, "mission", None, raising=False)
    mission = importlib.import_module("envfuzz.mission")
    return SimpleNamespace(module=mission, spin_hooks=spin_hooks)


def _goal_pose() -> SimpleNamespace:
    return SimpleNamespace(
        header=SimpleNamespace(frame_id="", stamp=None),
        pose=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0),
                             orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)),
    )


@pytest.fixture
def ros(monkeypatch):
    return _install_fake_ros(monkeypatch)


@pytest.fixture
def make_mission(ros, tmp_path):
    created = []

    def build(**flags):
        node = ros.module.Mission(tmp_path, **flags)
        created.append(node)
        return node

    yield build
    for node in created:
        for stream in (node.telemetry, node.scans, node.contacts, node.cliff, node.motion):
            if stream is not None:
                stream.close()


def _quaternion(angle: float) -> SimpleNamespace:
    return SimpleNamespace(x=0.0, y=0.0, z=math.sin(angle / 2), w=math.cos(angle / 2))


def _stamp(seconds: float) -> SimpleNamespace:
    return SimpleNamespace(nanoseconds=round(seconds * NS_PER_S))


def _odometry(seconds: float, x: float, y: float, heading: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        header=SimpleNamespace(stamp=_stamp(seconds)),
        pose=SimpleNamespace(pose=SimpleNamespace(
            position=SimpleNamespace(x=x, y=y), orientation=_quaternion(heading))),
    )


def _transform(x: float, y: float, heading: float) -> SimpleNamespace:
    return SimpleNamespace(translation=SimpleNamespace(x=x, y=y), rotation=_quaternion(heading))


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("angle", [0.0, 0.5, -1.2, math.pi / 2, 3.0])
def test_yaw_recovers_planar_heading_from_quaternion(ros, angle):
    # Arrange
    rotation = _quaternion(angle)

    # Act
    heading = ros.module.yaw(rotation)

    # Assert
    assert heading == pytest.approx(angle)


def test_mission_subscribes_only_to_requested_observation_streams(make_mission, tmp_path):
    # Act
    minimal = make_mission()
    full = make_mission(record_scans=True, record_contacts=True, record_cliff=True,
                        record_motion=True, map_localization=True)

    # Assert
    assert set(minimal.subscriptions) == {"/envfuzz/ground_truth", "/map"}
    assert minimal.initial_pose is None
    assert set(full.subscriptions) == {
        "/envfuzz/ground_truth", "/map", "/scan", "/envfuzz/bumper", "/envfuzz/cliff", "/imu", "/odom",
    }
    assert "/initialpose" in full.publishers
    assert full.parameter_overrides == [("use_sim_time", True)]
    for name in ("telemetry.jsonl", "scans.jsonl", "contacts.jsonl", "cliff.jsonl", "motion.jsonl"):
        assert (tmp_path / name).is_file()


def test_cliff_records_only_detection_transitions(make_mission, tmp_path):
    # Arrange
    node = make_mission(record_cliff=True)
    floor = SimpleNamespace(header=SimpleNamespace(stamp=_stamp(1.0)), ranges=[0.05, math.inf])
    edge = SimpleNamespace(header=SimpleNamespace(stamp=_stamp(2.0)), ranges=[0.5, 0.3])
    void = SimpleNamespace(header=SimpleNamespace(stamp=_stamp(3.0)), ranges=[math.inf, math.nan])

    # Act
    for message in (floor, edge, edge, floor, void):
        node.record_cliff(message)
    node.cliff.close()

    # Assert
    assert _rows(tmp_path / "cliff.jsonl") == [
        {"kind": "cliff", "sim_time": 2.0, "detected": True, "range_m": 0.3},
        {"kind": "cliff", "sim_time": 1.0, "detected": False, "range_m": 0.05},
        {"kind": "cliff", "sim_time": 3.0, "detected": True, "range_m": None},
    ]


def test_motion_stream_records_imu_and_wheel_odometry(make_mission, tmp_path):
    # Arrange
    node = make_mission(record_motion=True)
    imu = SimpleNamespace(
        header=SimpleNamespace(stamp=_stamp(0.5)),
        linear_acceleration=SimpleNamespace(x=0.1, y=0.2, z=9.8),
        angular_velocity=SimpleNamespace(x=0.0, y=0.0, z=0.3),
    )

    # Act
    node.record_imu(imu)
    node.record_wheel_odom(_odometry(0.6, 1.5, -0.5, heading=0.25))
    node.motion.close()

    # Assert
    imu_row, odom_row = _rows(tmp_path / "motion.jsonl")
    assert imu_row == {"kind": "imu", "sim_time": 0.5, "acceleration": [0.1, 0.2, 9.8],
                       "angular_velocity": [0.0, 0.0, 0.3]}
    assert odom_row["kind"] == "wheel_odometry"
    assert (odom_row["sim_time"], odom_row["x"], odom_row["y"]) == (0.6, 1.5, -0.5)
    assert odom_row["yaw"] == pytest.approx(0.25)


def test_contacts_are_debounced_per_sorted_collision_pair(make_mission, tmp_path):
    # Arrange
    node = make_mission(record_contacts=True)
    robot = SimpleNamespace(id=7, name="robot::bumper")
    chair = SimpleNamespace(id=3, name="chair::link")
    wall = SimpleNamespace(id=9, name="wall::link")

    def contacts(seconds, *pairs):
        return SimpleNamespace(
            header=SimpleNamespace(stamp=_stamp(seconds)),
            contacts=[SimpleNamespace(collision1=a, collision2=b) for a, b in pairs],
        )

    # Act
    node.record_contact(contacts(1.0, (robot, chair)))
    node.record_contact(contacts(1.05, (chair, robot), (robot, wall)))
    node.record_contact(contacts(1.3, (robot, chair)))
    node.contacts.close()

    # Assert
    rows = _rows(tmp_path / "contacts.jsonl")
    assert [(row["sim_time"], [item["id"] for item in row["collisions"]]) for row in rows] == [
        (1.0, [3, 7]), (1.05, [7, 9]), (1.3, [3, 7]),
    ]
    assert rows[0]["collisions"][0] == {"id": 3, "name": "chair::link"}


def test_scans_replace_non_finite_ranges_with_null(make_mission, tmp_path):
    # Arrange
    node = make_mission(record_scans=True)
    scan = SimpleNamespace(header=SimpleNamespace(stamp=_stamp(2.5)), angle_min=-1.0,
                           angle_increment=0.5, ranges=[1.0, math.inf, math.nan, 2.0])

    # Act
    node.record_scan(scan)
    node.scans.close()

    # Assert
    assert _rows(tmp_path / "scans.jsonl") == [
        {"sim_time": 2.5, "angle_min": -1.0, "angle_increment": 0.5, "ranges": [1.0, None, None, 2.0]},
    ]


def test_ground_truth_before_start_only_updates_origin(make_mission, tmp_path):
    # Arrange
    node = make_mission()

    # Act
    node.record(_odometry(1.0, 0.5, 0.25, heading=0.1))

    # Assert
    assert node.origin.x == 0.5 and node.origin.y == 0.25
    assert node.samples == 0
    assert node.distance == 0.0
    assert not node.pending


def test_ground_truth_accumulates_path_and_pairs_estimates(make_mission, ros, tmp_path):
    # Arrange
    node = make_mission()
    node.started = 0.0
    node.map_origin = (0.0, 0.0, 0.0)
    node.world_origin = ros.module.PoseSample(sim_time=0, x=0.0, y=0.0, yaw=0.0)
    node.transforms.transform = _transform(3.1, 4.0, 0.0)

    # Act
    node.record(_odometry(1.0, 0.0, 0.0))
    node.record(_odometry(2.0, 3.0, 4.0))
    node.record(_odometry(1.5, 9.0, 9.0))  # out-of-order sample is ignored
    node.telemetry.close()

    # Assert
    rows = _rows(tmp_path / "telemetry.jsonl")
    assert node.distance == pytest.approx(5.0)
    assert node.samples == 2
    assert node.paired == 2
    assert [row["ground_truth"]["sim_time"] for row in rows] == [1.0, 2.0]
    assert rows[1]["estimated"]["x"] == pytest.approx(3.1)
    assert node.squared_pose_error == pytest.approx(3.1**2 + 4.0**2 + 0.1**2)


def test_unpaired_samples_wait_for_a_transform_until_stale_or_final(make_mission, tmp_path):
    # Arrange
    node = make_mission()
    node.started = 0.0
    node.clock.seconds = 1.2

    # Act
    node.record(_odometry(1.0, 0.0, 0.0))
    held = len(node.pending)
    node.clock.seconds = 1.6
    node.flush_telemetry()
    released_when_stale = len(node.pending)
    node.record(_odometry(1.7, 0.1, 0.0))
    node.flush_telemetry(final=True)
    node.telemetry.close()

    # Assert
    assert held == 1
    assert released_when_stale == 0
    assert not node.pending
    assert [row["estimated"] for row in _rows(tmp_path / "telemetry.jsonl")] == [None, None]
    assert node.paired == 0


def test_estimate_maps_slam_pose_back_into_world_frame(make_mission, ros):
    # Arrange
    node = make_mission()
    node.map_origin = (1.0, 1.0, math.pi / 2)
    node.world_origin = ros.module.PoseSample(sim_time=0.0, x=2.0, y=3.0, yaw=0.0)
    node.transforms.transform = _transform(1.0, 2.0, math.pi / 2)

    # Act
    estimate = node.estimate(FakeTime(seconds=4.0))

    # Assert
    assert estimate.sim_time == 4.0
    assert estimate.x == pytest.approx(3.0)
    assert estimate.y == pytest.approx(3.0)
    assert estimate.yaw == pytest.approx(0.0)


def test_feedback_only_records_new_recovery_counts(make_mission):
    # Arrange
    node = make_mission()

    # Act
    for seconds, count in ((1.0, 0), (2.0, 1), (3.0, 1), (4.0, 3)):
        node.clock.seconds = seconds
        node.record_feedback(SimpleNamespace(feedback=SimpleNamespace(number_of_recoveries=count)))

    # Assert
    assert node.recoveries == [{"sim_time": 2.0, "count": 1}, {"sim_time": 4.0, "count": 3}]
    assert node.goal_recoveries == 3


def test_initial_pose_is_throttled_and_matches_origin(make_mission, ros):
    # Arrange
    node = make_mission(map_localization=True)
    publisher = node.publishers["/initialpose"]
    node.publish_initial_pose()  # no origin yet
    node.origin = ros.module.PoseSample(sim_time=0.0, x=1.0, y=-2.0, yaw=math.pi / 2)

    # Act
    for seconds in (1.0, 1.2, 1.6):
        node.clock.seconds = seconds
        node.publish_initial_pose()

    # Assert
    assert len(publisher.messages) == 2
    message = publisher.messages[0]
    assert message.header.frame_id == "map"
    assert (message.pose.pose.position.x, message.pose.pose.position.y) == (1.0, -2.0)
    assert ros.module.yaw(message.pose.pose.orientation) == pytest.approx(math.pi / 2)
    assert message.pose.covariance[0] == message.pose.covariance[7] == 0.25
    assert message.pose.covariance[35] == 0.0685


def test_initial_pose_is_not_published_without_map_localization(make_mission, ros):
    # Arrange
    node = make_mission()
    node.origin = ros.module.PoseSample(sim_time=0.0, x=1.0, y=1.0, yaw=0.0)

    # Act
    node.publish_initial_pose()

    # Assert
    assert node.publishers == {}
    assert node.last_initial_pose_time is None


def _occupancy(data: list[int], width: int, height: int) -> SimpleNamespace:
    return SimpleNamespace(
        header=SimpleNamespace(frame_id="map"),
        info=SimpleNamespace(width=width, height=height, resolution=0.5, origin=SimpleNamespace(
            position=SimpleNamespace(x=1.0, y=0.0), orientation=_quaternion(0.0))),
        data=data,
    )


def test_save_map_writes_aligned_ternary_grid(make_mission, ros, tmp_path):
    # Arrange
    node = make_mission()
    node.save_map()  # no map received yet
    node.map_origin = (0.0, 0.0, math.pi / 2)
    node.world_origin = ros.module.PoseSample(sim_time=0.0, x=5.0, y=5.0, yaw=0.0)
    node.record_map(_occupancy([-1, 0, 49, 50, 100, -3], width=3, height=2))

    # Act
    node.save_map()

    # Assert
    grid = json.loads((tmp_path / "slam-map.json").read_text())
    assert grid["data"] == [-1, 0, 0, 100, 100, -1]
    assert (grid["width"], grid["height"], grid["resolution"]) == (3, 2, 0.5)
    assert grid["origin"]["x"] == pytest.approx(5.0)
    assert grid["origin"]["y"] == pytest.approx(4.0)
    assert grid["origin"]["yaw"] == pytest.approx(-math.pi / 2)
    alignment = json.loads((tmp_path / "map-alignment.json").read_text())
    assert alignment["map_anchor"] == {"x": 0.0, "y": 0.0, "yaw_offset": math.pi / 2}
    assert alignment["source_frame"] == "map"


def test_save_map_skips_grids_over_the_cell_cap(make_mission, ros, tmp_path):
    # Arrange
    node = make_mission()
    node.map_origin = (0.0, 0.0, 0.0)
    node.world_origin = ros.module.PoseSample(sim_time=0.0, x=0.0, y=0.0, yaw=0.0)
    node.latest_map = _occupancy([0] * 1_000_001, width=1_000_001, height=1)

    # Act
    node.save_map()

    # Assert
    assert not (tmp_path / "slam-map.json").exists()
    assert not (tmp_path / "map-alignment.json").exists()


class FakeLaunch:
    def __init__(self, exit_after_polls: int | None = None, returncode: int = 0) -> None:
        self.polls = 0
        self.exit_after_polls = exit_after_polls
        self.returncode = None
        self.final_code = returncode
        self.signals = []
        self.waited = False

    def poll(self):
        self.polls += 1
        if self.exit_after_polls is not None and self.polls > self.exit_after_polls:
            self.returncode = self.final_code
        return self.returncode

    def send_signal(self, value) -> None:
        self.signals.append(value)
        self.returncode = -value

    def wait(self) -> int:
        self.waited = True
        return self.returncode


def _ready_for_run(node, ros, *, lifecycle_states=(ACTIVE_STATE_ID,), transform_failures=0):
    node.lifecycle.states = list(lifecycle_states)
    node.transforms.transform = _transform(0.0, 0.0, math.pi / 2)
    node.transforms.failures_before_success = transform_failures

    def deliver_origin(spun):
        if spun.origin is None:
            spun.record(_odometry(spun.clock.seconds, 1.0, 2.0))

    ros.spin_hooks.append(deliver_origin)


def _goal(status: int = SUCCEEDED_STATUS, accepted: bool = True, result_done: bool = True):
    handle = FakeGoalHandle(accepted, FakeFuture(SimpleNamespace(status=status), done=result_done))
    return FakeFuture(handle), handle


def test_run_completes_goals_with_map_frame_requests_and_metrics(make_mission, ros, tmp_path):
    # Arrange
    node = make_mission(map_localization=True)
    _ready_for_run(node, ros, lifecycle_states=(1, ACTIVE_STATE_ID), transform_failures=1)
    first, _ = _goal()
    second, _ = _goal()
    node.client.script = [first, second]
    goals = [{"x": 2.0, "y": 2.0, "yaw": 0.0}, {"x": 1.0, "y": 3.0, "yaw": math.pi / 2}]

    # Act
    result = node.run(goals, FakeLaunch(), timeout=30)

    # Assert
    assert result["status"] == "completed"
    assert result["classification"] == "behavior"
    assert result["reason"] is None
    assert node.lifecycle.calls == 2
    assert node.publishers["/initialpose"].messages
    request, callback = node.client.requests[0]
    assert callback == node.record_feedback
    assert request.pose.header.frame_id == "map"
    assert request.pose.pose.position.x == pytest.approx(0.0)
    assert request.pose.pose.position.y == pytest.approx(1.0)
    second_request = node.client.requests[1][0]
    assert second_request.pose.pose.position.x == pytest.approx(-1.0)
    assert second_request.pose.pose.position.y == pytest.approx(0.0)
    assert ros.module.yaw(second_request.pose.pose.orientation) == pytest.approx(math.pi)
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["goals_reached"] == 2
    assert metrics["goal_count"] == 2
    assert metrics["position_rmse_m"] is None
    assert metrics["recoveries"] == []


def test_run_waits_for_action_server_before_starting(make_mission, ros):
    # Arrange
    node = make_mission()
    _ready_for_run(node, ros)
    node.client.ready = False
    future, _ = _goal()
    node.client.script = [future]

    def server_comes_up(spun):
        if spun.clock.seconds >= 0.5:
            spun.client.ready = True

    ros.spin_hooks.append(server_comes_up)

    # Act
    result = node.run([{"x": 1.0, "y": 2.0, "yaw": 0.0}], FakeLaunch(), timeout=30)

    # Assert
    assert result["status"] == "completed"
    assert node.started >= 0.5


def test_run_reports_failed_navigation_status(make_mission, ros, tmp_path):
    # Arrange
    node = make_mission()
    _ready_for_run(node, ros)
    future, _ = _goal(status=ABORTED_STATUS)
    node.client.script = [future]

    # Act
    result = node.run([{"x": 1.0, "y": 2.0, "yaw": 0.0}], FakeLaunch(), timeout=30)

    # Assert
    assert result["status"] == "mission_failed"
    assert result["reason"] == f"Nav2 reported action status {ABORTED_STATUS}"
    assert json.loads((tmp_path / "metrics.json").read_text())["goals_reached"] == 0


def test_run_times_out_when_goal_is_never_accepted(make_mission, ros, tmp_path):
    # Arrange
    node = make_mission()
    _ready_for_run(node, ros)
    node.client.script = [FakeFuture(done=False)]

    # Act
    result = node.run([{"x": 1.0, "y": 2.0, "yaw": 0.0}], FakeLaunch(), timeout=0.5)

    # Assert
    assert result["status"] == "timeout"
    assert result["reason"] == "Simulation-time mission limit expired"
    assert 0.5 <= result["sim_duration"] < 0.5 + 2 * SPIN_STEP_S


def test_run_cancels_goal_when_result_times_out(make_mission, ros):
    # Arrange
    node = make_mission()
    _ready_for_run(node, ros)
    future, handle = _goal(result_done=False)
    node.client.script = [future]

    # Act
    result = node.run([{"x": 1.0, "y": 2.0, "yaw": 0.0}], FakeLaunch(), timeout=0.3)

    # Assert
    assert result["status"] == "timeout"
    assert handle.cancelled


def test_run_raises_when_nav2_rejects_goal(make_mission, ros):
    # Arrange
    node = make_mission()
    _ready_for_run(node, ros)
    future, _ = _goal(accepted=False)
    node.client.script = [future]

    # Act / Assert
    with pytest.raises(RuntimeError, match="rejected"):
        node.run([{"x": 1.0, "y": 2.0, "yaw": 0.0}], FakeLaunch(), timeout=30)


def test_spin_raises_when_launch_process_exits(make_mission):
    # Arrange
    node = make_mission()
    launch = FakeLaunch(exit_after_polls=0, returncode=7)

    # Act / Assert
    with pytest.raises(RuntimeError, match="code 7"):
        node.spin(launch)


class FakePopen:
    instances: ClassVar[list] = []

    def __init__(self, command) -> None:
        self.command = command
        self.launch = FakeLaunch()
        FakePopen.instances.append(self)

    def __enter__(self):
        return self.launch

    def __exit__(self, *exc) -> None:
        return None


def _main_setup(ros, monkeypatch, tmp_path, scenario: dict, *, outcome=None, env=None):
    (tmp_path / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    FakePopen.instances = []
    monkeypatch.setattr(ros.module, "subprocess", SimpleNamespace(Popen=FakePopen))
    copies = []
    monkeypatch.setattr(ros.module, "shutil", SimpleNamespace(
        copyfile=lambda src, dst: copies.append((src, dst))))
    for name in ("ENVFUZZ_LOCALIZATION_MAP", "ENVFUZZ_RECORD_CONTACTS", "ENVFUZZ_RECORD_CLIFF",
                 "ENVFUZZ_RECORD_MOTION", "ENVFUZZ_NAVIGATION_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["mission", str(tmp_path), "12.5"])
    created = []
    real_init = ros.module.Mission.__init__

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created.append((self, kwargs))

    def scripted_run(self, goals, launch, timeout):
        if isinstance(outcome, Exception):
            raise outcome
        return {"status": "completed", "goals": goals, "timeout": timeout}

    monkeypatch.setattr(ros.module.Mission, "__init__", tracking_init)
    monkeypatch.setattr(ros.module.Mission, "run", scripted_run)
    return copies, created


def test_main_builds_launch_command_and_writes_mission_result(ros, monkeypatch, tmp_path):
    # Arrange
    scenario = {"start": {"x": 0.5, "y": -1.0, "yaw": 0.25}, "goals": [{"x": 2, "y": 0, "yaw": 0}],
                "events": [{"at": 1}]}
    copies, created = _main_setup(ros, monkeypatch, tmp_path, scenario, env={
        "ENVFUZZ_LOCALIZATION_MAP": "/maps/layout.yaml", "ENVFUZZ_RECORD_CONTACTS": "1",
        "ENVFUZZ_RECORD_CLIFF": "1", "ENVFUZZ_RECORD_MOTION": "1", "ENVFUZZ_NAVIGATION_BACKEND": "amcl",
    })

    # Act
    ros.module.main()

    # Assert
    command = FakePopen.instances[0].command
    assert command[:4] == ["ros2", "launch", "envfuzz_sim", "headless.launch.py"]
    assert f"world:={tmp_path / 'world.sdf'}" in command
    assert ["x_pose:=0.5", "y_pose:=-1.0", "yaw:=0.25"] == command[5:8]
    assert f"params_file:={tmp_path / 'nav2.yaml'}" in command
    assert command[-1] == "map:=/maps/layout.yaml"
    assert copies == [(Path("/fake/share/nav2_bringup") / "params" / "nav2_params.yaml",
                       tmp_path / "nav2.yaml")]
    node, flags = created[0]
    assert flags == {"record_scans": True, "record_contacts": True, "record_cliff": True,
                     "record_motion": True, "map_localization": True}
    assert json.loads((tmp_path / "mission.json").read_text()) == {
        "status": "completed", "goals": scenario["goals"], "timeout": 12.5,
    }
    launch = FakePopen.instances[0].launch
    assert launch.signals == [signal.SIGINT] and launch.waited
    assert node.destroyed
    assert all(stream.closed for stream in (node.telemetry, node.scans, node.contacts,
                                            node.cliff, node.motion))


def test_main_keeps_existing_params_and_cleans_up_after_failure(ros, monkeypatch, tmp_path):
    # Arrange
    (tmp_path / "nav2.yaml").write_text("custom: true\n", encoding="utf-8")
    scenario = {"start": {"x": 0, "y": 0, "yaw": 0}, "goals": []}
    copies, created = _main_setup(ros, monkeypatch, tmp_path, scenario,
                                  outcome=RuntimeError("launch died"))

    # Act
    with pytest.raises(RuntimeError, match="launch died"):
        ros.module.main()

    # Assert
    command = FakePopen.instances[0].command
    assert not any(part.startswith("map:=") for part in command)
    assert copies == []
    node, flags = created[0]
    assert flags["record_scans"] is False
    assert flags["map_localization"] is False
    assert node.scans is None
    assert node.telemetry.closed
    assert node.destroyed
    assert not (tmp_path / "mission.json").exists()
