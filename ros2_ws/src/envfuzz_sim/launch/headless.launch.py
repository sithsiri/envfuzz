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
from launch_ros.actions import LifecycleNode, Node

# CPU lidar-only RTAB-Map, following rtabmap_demos turtlebot3_scan.launch.py (0.23.13):
# 2D laser scans plus wheel odometry, ICP registration constrained to 3 DoF, no camera.
RTABMAP_LIDAR_PARAMETERS = {
    "frame_id": "base_footprint",
    "subscribe_depth": False,
    "subscribe_rgb": False,
    "subscribe_scan": True,
    "approx_sync": True,
    "Reg/Strategy": "1",
    "Reg/Force3DoF": "true",
    "RGBD/NeighborLinkRefining": "True",
    "Grid/RangeMin": "0.2",
    "Optimizer/GravitySigma": "0",
}


def navigation_mode(backend: str | None, camera_profile: bool) -> dict[str, str]:
    """Return the Nav2 bringup arguments that select SLAM Toolbox, AMCL or an external RTAB-Map map."""
    if backend == "rtabmap_lidar":
        return {"slam": "False", "use_localization": "False"}
    if camera_profile or backend == "amcl":
        return {"slam": "False"}
    return {"slam": "True"}


def rtabmap_lidar_node() -> Node:
    """RTAB-Map publishes /map and map->odom from /scan and /odom for Nav2."""
    return Node(
        package="rtabmap_slam", executable="rtabmap", output="screen",
        parameters=[{**RTABMAP_LIDAR_PARAMETERS, "use_sim_time": True}],
        remappings=[("scan", "/scan"), ("odom", "/odom")],
        arguments=["-d"],
    )


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


def _add_rgbd_camera(base_link: ET.Element) -> None:
    sensor = ET.SubElement(base_link, "sensor", name="envfuzz_rgbd", type="rgbd_camera")
    ET.SubElement(sensor, "pose").text = "0.12 0 0.20 0 0 0"
    ET.SubElement(sensor, "topic").text = "/envfuzz/camera"
    ET.SubElement(sensor, "always_on").text = "true"
    ET.SubElement(sensor, "update_rate").text = os.environ.get("ENVFUZZ_CAMERA_HZ", "15")
    camera = ET.SubElement(sensor, "camera")
    ET.SubElement(camera, "horizontal_fov").text = "1.0471975512"
    image = ET.SubElement(camera, "image")
    ET.SubElement(image, "width").text = os.environ.get("ENVFUZZ_CAMERA_WIDTH", "320")
    ET.SubElement(image, "height").text = os.environ.get("ENVFUZZ_CAMERA_HEIGHT", "240")
    ET.SubElement(image, "format").text = "R8G8B8"
    clip = ET.SubElement(camera, "clip")
    ET.SubElement(clip, "near").text = "0.1"
    ET.SubElement(clip, "far").text = "8.0"


def robot_sdf(
    turtlebot: Path, record_contacts: bool = False, record_cliff: bool = False,
    camera_profile: bool = False,
) -> str:
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
    if camera_profile:
        _add_rgbd_camera(base_link)
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
    localization_map = LaunchConfiguration("map")

    record_contacts = os.environ.get("ENVFUZZ_RECORD_CONTACTS") == "1"
    record_cliff = os.environ.get("ENVFUZZ_RECORD_CLIFF") == "1"
    camera_profile = os.environ.get("ENVFUZZ_CAMERA_PROFILE") == "1"
    keepout_mask = os.environ.get("ENVFUZZ_KEEPOUT_FILTER_MASK")
    backend = os.environ.get("ENVFUZZ_NAVIGATION_BACKEND")
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
    if camera_profile:
        observation_bridges.append(Node(
            package="ros_gz_bridge", executable="parameter_bridge", name="camera_bridge",
            arguments=[
                "/envfuzz/camera/image@sensor_msgs/msg/Image[gz.msgs.Image",
                "/envfuzz/camera/depth_image@sensor_msgs/msg/Image[gz.msgs.Image",
                "/envfuzz/camera/camera_info@sensor_msgs/msg/CameraInfo[gz.msgs.CameraInfo",
            ],
            parameters=[{"use_sim_time": True}], output="screen",
        ))

    return LaunchDescription([
        DeclareLaunchArgument("world", description="Absolute path to the compiled world.sdf"),
        DeclareLaunchArgument("x_pose", default_value="0.0"),
        DeclareLaunchArgument("y_pose", default_value="0.0"),
        DeclareLaunchArgument("yaw", default_value="0.0"),
        DeclareLaunchArgument("params_file", default_value=str(nav2 / "params" / "nav2_params.yaml")),
        DeclareLaunchArgument("map", default_value=""),
        AppendEnvironmentVariable("GZ_SIM_RESOURCE_PATH", str(turtlebot / "models")),
        AppendEnvironmentVariable("GZ_SIM_RESOURCE_PATH", str(turtlebot.parent)),
        AppendEnvironmentVariable("GZ_SIM_SYSTEM_PLUGIN_PATH", str(Path(get_package_prefix("envfuzz_sim")) / "lib")),
        Node(
            package="ros_gz_sim", executable="create",
            arguments=[
                "-world", "envfuzz", "-name", "turtlebot3_waffle",
                "-string", robot_sdf(turtlebot, record_contacts, record_cliff, camera_profile),
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
        *([LifecycleNode(
            package="nav2_map_server", executable="map_server", name="keepout_filter_mask_server",
            namespace="",
            output="screen", parameters=[{
                "yaml_filename": keepout_mask, "use_sim_time": True,
            }], remappings=[("map", "/keepout_filter_mask")],
        ), LifecycleNode(
            package="nav2_map_server", executable="costmap_filter_info_server",
            name="costmap_filter_info_server", namespace="", output="screen", parameters=[{
                "use_sim_time": True, "type": 0, "filter_info_topic": "/costmap_filter_info",
                "mask_topic": "/keepout_filter_mask", "base": 0.0, "multiplier": 1.0,
            }],
        ), Node(
            package="nav2_lifecycle_manager", executable="lifecycle_manager",
            name="lifecycle_manager_keepout_filter", output="screen", parameters=[{
                "use_sim_time": True, "autostart": True,
                "node_names": ["keepout_filter_mask_server", "costmap_filter_info_server"],
            }],
        )] if keepout_mask else []),
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
        *([IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(Path(get_package_share_directory("rtabmap_launch")) /
                                               "launch" / "rtabmap.launch.py")),
            launch_arguments={
                "use_sim_time": "true",
                "frame_id": "base_link",
                "rgb_topic": "/envfuzz/camera/image",
                "depth_topic": "/envfuzz/camera/depth_image",
                "camera_info_topic": "/envfuzz/camera/camera_info",
                "approx_sync": "true",
                "visual_odometry": "true",
                "rtabmap_viz": "false",
                "rviz": "false",
                "delete_db_on_start": "true",
            }.items(),
        )] if camera_profile else []),
        *([rtabmap_lidar_node()] if backend == "rtabmap_lidar" else []),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(nav2 / "launch" / "bringup_launch.py")),
            launch_arguments={
                **navigation_mode(backend, camera_profile),
                "use_sim_time": "true",
                "autostart": "true",
                "use_composition": "False",
                "params_file": params,
                "map": localization_map,
            }.items(),
        ),
    ])
