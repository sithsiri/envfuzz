# Headless contact, cliff, and motion observations

The CPU headless simulator accepts opt-in observation flags in its version 1
profile. Pass the profile to `envfuzz run`. The result bundle archives the
profile for replay. Existing scenarios and profiles retain their defaults.

| Flag | Bundle file | Observation |
| --- | --- | --- |
| `record_contacts` | `contacts.jsonl` | Bumper contact with simulation timestamp and collision pair. Continuous reports for one pair are grouped until there is a gap of more than 0.1 simulated seconds. |
| `record_cliff` | `cliff.jsonl` | Detection transitions from a down-facing three-ray sensor at the front of the robot. A range of at least 0.20 m or the absence of a finite return indicates a cliff. |
| `record_motion` | `motion.jsonl` | Timestamped IMU acceleration, angular velocity, and wheel-odometry poses for comparison with ground truth in `telemetry.jsonl`. |

The observation streams are diagnostic. They do not feed navigation commands
or change oracle tier scoring.

## Floor geometry and friction

Version 2 scenarios can define up to eight `floor_drops` and eight
`floor_patches`. Each rectangle has `min_x`, `max_x`, `min_y` and
`max_y` in world meters. A drop has a `depth_m` from 0.05 to 2 m. A patch
has a friction coefficient from 0 to 2. Regions must fit inside the world
and their interiors must be disjoint. The compiler tiles the floor and lowers
the tiles in each drop, leaving a physical ledge and recessed floor.

Drops enforce start and goal clearance and count as hazards in the
ground-truth reachability check. The planar LiDAR reference grid still uses
obstacles at the LiDAR's scan height.

## Reproduce the checks

Run these commands in the headless ROS and Gazebo environment.

```sh
envfuzz run examples/bumper-contact.yaml --profile examples/bumper-profile.yaml --output runs/bumper
envfuzz run examples/cliff-drop.yaml --profile examples/cliff-profile.yaml --output runs/cliff
envfuzz run examples/friction-patch.yaml --profile examples/motion-profile.yaml --output runs/friction
ENVFUZZ_GAZEBO_TEST=1 python -m pytest tests/test_bumper.py tests/test_floor_sensors.py
```

The controlled bumper fixture reported contact with the low threshold at
4.594 simulated seconds. The 0.5 m ledge produced a cliff detection with a
0.631 m return. The cliff test requires a finite detected range of at least
0.4 m.

On ARM64 Docker Desktop with a two-CPU quota and 1536 MiB memory, the uniform
floor produced 0.002 m of longitudinal wheel-odometry displacement error.
The zero-friction patch produced 1.850 m. The paired check allows less than
0.25 m on the control floor and requires the patch error to exceed the
control by more than 0.30 m. These tolerances apply to this fixture.
