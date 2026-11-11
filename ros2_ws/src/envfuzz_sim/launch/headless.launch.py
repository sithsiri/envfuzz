"""Start Gazebo and the Nav2 TurtleBot stack in a compiled envfuzz world."""

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


def robot_sdf(turtlebot: Path) -> str:
    description = xacro.process_file(
        str(turtlebot / "urdf" / "gz_waffle.sdf.xacro"), mappings={"namespace": ""}
    )
    sdf = ET.fromstring(description.toxml())
    model = sdf.find("model")
    collision = model.find("./link[@name='base_link']/collision[@name='base_collision']")
    collision.find("pose").text = "0 0 0.048 0 0 0"
    geometry = collision.find("geometry")
    geometry.clear()
    cylinder = ET.SubElement(geometry, "cylinder")
    ET.SubElement(cylinder, "radius").text = "0.22"
    ET.SubElement(cylinder, "length").text = "0.089"
    sensor = model.find("./link[@name='base_scan']/sensor")
    if "ENVFUZZ_LIDAR_HZ" in os.environ:
        sensor.find("update_rate").text = os.environ["ENVFUZZ_LIDAR_HZ"]
    if "ENVFUZZ_LIDAR_RAYS" in os.environ:
        sensor.find(".//samples").text = os.environ["ENVFUZZ_LIDAR_RAYS"]
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
                "-world", "envfuzz", "-name", "turtlebot3_waffle", "-string", robot_sdf(turtlebot),
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
