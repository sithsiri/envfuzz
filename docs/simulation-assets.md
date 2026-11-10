# Phase 1 simulation assets

The scenario compiler generates the room floor, walls, and obstacles from SDF
primitives. The default mission and an example restricted polygon are in
`examples/scenario.yaml`. The polygon is stored as scenario metadata and
evaluated against ground-truth poses.

The TurtleBot3 Waffle model and meshes come from the installed
`nav2_minimal_tb3_sim` package. Its package manifest declares Apache 2.0 and the
Debian distribution includes the package copyright record under
`/usr/share/doc/ros-jazzy-nav2-minimal-tb3-sim/copyright`. EnvFuzz modifies the
spawned SDF in memory to use a circular 0.22 m chassis collider and to publish
Gazebo ground truth. The upstream wheel, differential-drive, and sensor
descriptions are loaded from the installed package.

| Interface | Data |
| --- | --- |
| `/clock` | Gazebo simulation time |
| `/scan` | 360-ray planar `sensor_msgs/msg/LaserScan` |
| `/odom` | Wheel odometry |
| `/envfuzz/ground_truth` | Gazebo odometry in world coordinates |
| `/tf`, `/tf_static` | Robot transforms and SLAM map alignment |
| `/map` | SLAM occupancy grid |
| `/cmd_vel` | Nav2 velocity commands to the simulated differential drive |
| `/navigate_to_pose` | Nav2 goal action |
