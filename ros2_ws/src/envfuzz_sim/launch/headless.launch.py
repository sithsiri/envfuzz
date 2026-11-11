"""Start Gazebo and the Nav2 TurtleBot stack in a compiled envfuzz world."""

import math
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import xacro
from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    AppendEnvironmentVariable, DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _add_cliff_sensor(base_link: ET.Element) -> None:
    sensor = ET.SubElement(base_link, "sensor", name="envfuzz_cliff", type="gpu_lidar")
    ET.SubElement(sensor, "pose").text = "0.25 0 0.12 0 1.5707963267948966 0"
    ET.SubElement(sensor, "topic").text = "/envfuzz/cliff"
    ET.SubElement(sensor, "always_on").text = "true"
    ET.SubElement(sensor, "update_rate").text = "20"
    lidar = ET.SubElement(sensor, "lidar")
    horizontal = ET.SubElement(ET.SubElement(lidar, "scan"), "horizontal")
    for name, value in (("samples", "3"), ("resolution", "1"), ("min_angle", "-0.025"), ("max_angle", "0.025")):
        ET.SubElement(horizontal, name).text = value
    limits = ET.SubElement(lidar, "range")
    for name, value in (("min", "0.01"), ("max", "3"), ("resolution", "0.001")):
        ET.SubElement(limits, name).text = value


def robot_sdf(turtlebot: Path, record_contacts: bool = False, record_cliff: bool = False) -> str:
    description = xacro.process_file(
        str(turtlebot / "urdf" / "gz_waffle.sdf.xacro"), mappings={"namespace": ""}
    )
    sdf = ET.fromstring(description.toxml())
    model = sdf.find("model")
    base_link = model.find("./link[@name='base_link']")
    collision = base_link.find("./collision[@name='base_collision']")
    collision.find("pose").text = "0 0 0.048 0 0 0"
    geometry = collision.find("geometry")
    geometry.clear()
    cylinder = ET.SubElement(geometry, "cylinder")
    ET.SubElement(cylinder, "radius").text = "0.22"
    ET.SubElement(cylinder, "length").text = "0.089"
    if os.environ.get("ENVFUZZ_ROBOT_MODEL") == "compact_vacuum":
        collision.find("pose").text = "0 0 0.0335 0 0 0"
        cylinder.find("length").text = "0.06"
        visual = base_link.find("visual")
        visual_pose = visual.find("pose")
        if visual_pose is None:
            visual_pose = ET.SubElement(visual, "pose")
        visual_pose.text = collision.findtext("pose")
        visual_geometry = visual.find("geometry")
        visual_geometry.clear()
        chassis = ET.SubElement(visual_geometry, "cylinder")
        ET.SubElement(chassis, "radius").text = "0.22"
        ET.SubElement(chassis, "length").text = "0.06"
    if record_contacts:
        bumper = ET.SubElement(base_link, "sensor", name="envfuzz_bumper", type="contact")
        contact = ET.SubElement(bumper, "contact")
        ET.SubElement(contact, "collision").text = "base_collision"
        ET.SubElement(contact, "topic").text = "/envfuzz/bumper"
    if record_cliff:
        _add_cliff_sensor(base_link)
    sensor = model.find("./link[@name='base_scan']/sensor")
    if "ENVFUZZ_LIDAR_HZ" in os.environ:
        sensor.find("update_rate").text = os.environ["ENVFUZZ_LIDAR_HZ"]
    if "ENVFUZZ_LIDAR_RAYS" in os.environ:
        sensor.find(".//samples").text = os.environ["ENVFUZZ_LIDAR_RAYS"]
    lidar = sensor.find("lidar")
    if lidar is None:
        lidar = sensor.find("ray")
    if "ENVFUZZ_LIDAR_FOV_DEG" in os.environ:
        half_fov = math.radians(float(os.environ["ENVFUZZ_LIDAR_FOV_DEG"])) / 2
        horizontal = lidar.find("./scan/horizontal")
        horizontal.find("min_angle").text = str(-half_fov)
        horizontal.find("max_angle").text = str(half_fov)
    if "ENVFUZZ_LIDAR_NOISE_M" in os.environ:
        existing = lidar.find("noise")
        if existing is not None:
            lidar.remove(existing)
        noise = ET.SubElement(lidar, "noise")
        ET.SubElement(noise, "type").text = "gaussian"
        ET.SubElement(noise, "mean").text = "0"
        ET.SubElement(noise, "stddev").text = os.environ["ENVFUZZ_LIDAR_NOISE_M"]
    plugin = ET.SubElement(
        model, "plugin", filename="gz-sim-odometry-publisher-system",
        name="gz::sim::systems::OdometryPublisher",
    )
    ET.SubElement(plugin, "odom_frame").text = "world"
    ET.SubElement(plugin, "odom_topic").text = "/envfuzz/ground_truth"
    ET.SubElement(plugin, "odom_publish_frequency").text = "10"
    return ET.tostring(sdf, encoding="unicode")


def generate_launch_description():
    turtlebot = Path(get_package_share_directory("nav2_minimal_tb3_sim"))
    nav2 = Path(get_package_share_directory("nav2_bringup"))
    robot_description = (turtlebot / "urdf" / "turtlebot3_waffle.urdf").read_text(encoding="utf-8")
    world = LaunchConfiguration("world")
    params = LaunchConfiguration("params_file")

    record_contacts = os.environ.get("ENVFUZZ_RECORD_CONTACTS") == "1"
    record_cliff = os.environ.get("ENVFUZZ_RECORD_CLIFF") == "1"
    observation_bridges = []
    if record_contacts:
        observation_bridges.append(Node(
            package="ros_gz_bridge", executable="parameter_bridge", name="bumper_bridge",
            arguments=["/envfuzz/bumper@ros_gz_interfaces/msg/Contacts[gz.msgs.Contacts"],
            parameters=[{"use_sim_time": True}],
            output="screen",
        ))
    if record_cliff:
        observation_bridges.append(Node(
            package="ros_gz_bridge", executable="parameter_bridge", name="cliff_bridge",
            arguments=["/envfuzz/cliff@sensor_msgs/msg/LaserScan[gz.msgs.LaserScan"],
            parameters=[{"use_sim_time": True}],
            output="screen",
        ))

    return LaunchDescription([
        DeclareLaunchArgument("world", description="Absolute path to the compiled world.sdf"),
        DeclareLaunchArgument("x_pose", default_value="0.0"),
        DeclareLaunchArgument("y_pose", default_value="0.0"),
        DeclareLaunchArgument("yaw", default_value="0.0"),
        DeclareLaunchArgument("params_file", default_value=str(nav2 / "params" / "nav2_params.yaml")),
        AppendEnvironmentVariable("GZ_SIM_RESOURCE_PATH", str(turtlebot / "models")),
        AppendEnvironmentVariable("GZ_SIM_RESOURCE_PATH", str(turtlebot.parent)),
        AppendEnvironmentVariable("GZ_SIM_SYSTEM_PLUGIN_PATH", str(Path(get_package_prefix("envfuzz_sim")) / "lib")),
        Node(
            package="ros_gz_sim", executable="create",
            arguments=[
                "-world", "envfuzz", "-name", "turtlebot3_waffle",
                "-string", robot_sdf(turtlebot, record_contacts, record_cliff),
                "-x", LaunchConfiguration("x_pose"), "-y", LaunchConfiguration("y_pose"),
                "-z", "0.01", "-Y", LaunchConfiguration("yaw"),
            ],
            output="screen",
        ),
        Node(
            package="ros_gz_bridge", executable="parameter_bridge",
            parameters=[{
                "use_sim_time": True,
                "config_file": str(turtlebot / "configs" / "turtlebot3_waffle_bridge.yaml"),
            }],
            output="screen",
        ),
        Node(
            package="ros_gz_bridge", executable="parameter_bridge", name="ground_truth_bridge",
            arguments=["/envfuzz/ground_truth@nav_msgs/msg/Odometry[gz.msgs.Odometry"],
            parameters=[{"use_sim_time": True}],
            output="screen",
        ),
        *observation_bridges,
        ExecuteProcess(
            cmd=["gz", "sim", "-r", "-s", "--headless-rendering", world],
            output="screen",
        ),
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"use_sim_time": True, "robot_description": robot_description}],
            output="screen",
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(nav2 / "launch" / "bringup_launch.py")),
            launch_arguments={
                "slam": "True",
                "use_sim_time": "true",
                "autostart": "true",
                "use_composition": "False",
                "params_file": params,
            }.items(),
        ),
    ])
