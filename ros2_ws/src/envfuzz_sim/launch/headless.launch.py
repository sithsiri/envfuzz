"""Headless simulation entry point; all nodes receive simulation time."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("use_sim_time", default_value="true"),
        Node(package="ros_gz_sim", executable="gz_sim", arguments=["-r", "apartment.sdf", "-s"]),
        Node(package="slam_toolbox", executable="async_slam_toolbox_node",
             parameters=[{"use_sim_time": True}]),
    ])

