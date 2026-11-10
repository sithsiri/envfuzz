# ADR 0003. Use Mesa software rendering for CPU LiDAR

## Decision

Use Gazebo Harmonic's standard planar LiDAR with OGRE2 and Mesa software
rendering. Start the server with `--headless-rendering`, set
`LIBGL_ALWAYS_SOFTWARE=1`, and leave `DISPLAY` unset. Keep the ROS
`/scan` message contract if the rendering implementation changes later.

## Measurement

The pinned Linux ARM64 image ran on Docker Desktop with eight VM CPUs. The
probe observed the loaded example apartment with the robot stationary and the
navigation and SLAM stack running. No GPU or X server was passed to the
container. Gazebo Sim was 8.15.0.

| Rays per scan | Scans | Sample window | Scans per simulated second | Scans per wall second | Real-time factor | Container CPU cores |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 360 | 94 | 20.10 s | 5.00 | 4.72 | 0.94× | 2.41 |

The rates use the first and last delivered scan timestamps. CPU usage is the
change in container cgroup CPU time divided by elapsed wall time. It includes
Gazebo, Nav2, SLAM, the bridge, and the probe. The host was also running build
and acceptance work, so this is an observed rate for that window. It is not a
throughput guarantee.

Reproduce the probe from the repository root after building the image.

```sh
docker run --rm -i --entrypoint bash envfuzz:jazzy -c '
  source /opt/ros/jazzy/setup.bash
  source /opt/envfuzz-sim/local_setup.bash
  python3 -
' < docker/lidar_probe.py
```

## Implications

The measured rate supports the Phase 1 static-layout episodes. Standard
simulated ranges depend on rendered obstacle geometry. Surface properties
such as transparency and reflectivity need dedicated sensor modeling and
validation before they can serve as benchmark variables.
