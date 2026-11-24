# Simulation assets

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

## Robot and navigation profiles

Profiles are version 1 YAML files for organizing robot and Nav2 configurations.
The default profile uses the TurtleBot3 Waffle, SLAM Toolbox, planar LiDAR,
and CPU-only execution. A profile can select another robot or navigation
backend and bundle the resolved settings with each result for replay.

```sh
envfuzz baseline examples/scenario.yaml \
  --profile examples/compact-vacuum-profile.yaml --output runs/compact-baseline
envfuzz run examples/scenario.yaml \
  --profile examples/compact-vacuum-profile.yaml --baseline runs/compact-baseline/baseline.json \
  --output runs/compact
envfuzz replay runs/compact --output runs/compact-replay
```

Scoring baselines must use the same scenario, Nav2 settings, robot, and profile.
Existing bundles without a profile retain their implicit default settings.

Available examples include AMCL localization, LiDAR-only RTAB-Map, and an
optional GPU-gated RTAB-Map camera profile. The LiDAR-only and camera profiles
require matching baselines. The camera profile is not available on a CPU-only
worker.

## Optional observations

Profiles can opt into diagnostic streams that do not feed navigation or oracle
scoring:

| Flag | Stream | Description |
| --- | --- | --- |
| `record_contacts` | `contacts.jsonl` | Bumper collision pairs and simulation timestamps |
| `record_cliff` | `cliff.jsonl` | Down-facing cliff sensor transitions |
| `record_motion` | `motion.jsonl` | IMU, wheel odometry, and timestamped poses |

Version 2 scenarios can declare floor drops and friction patches. They are
validated with the rest of the scenario, compiled into the world, and archived
with the run bundle.

```sh
envfuzz run examples/bumper-contact.yaml --profile examples/bumper-profile.yaml \
  --output runs/bumper
envfuzz run examples/cliff-drop.yaml --profile examples/cliff-profile.yaml \
  --output runs/cliff
envfuzz run examples/friction-patch.yaml --profile examples/motion-profile.yaml \
  --output runs/friction
```
