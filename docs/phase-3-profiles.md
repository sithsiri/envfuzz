# CPU robot and navigation profiles

Profiles are version 1 YAML files chosen by the organizer or local operator.
The robot allowlist includes the default `turtlebot3_waffle` and
`compact_vacuum`. The navigation backends are:

| Backend | Selected by | Hardware | Status |
| --- | --- | --- | --- |
| `slam_toolbox` | Default (no profile) | CPU | Supported |
| `amcl` | `examples/amcl-localization-profile.yaml` | CPU | Live-verified, see the [backend comparison](phase-3-backend-comparison.md) |
| `rtabmap_lidar` | `examples/rtabmap-lidar-profile.yaml` | CPU, no camera | Implemented, live verification pending |
| `rtabmap` | `examples/rtabmap-camera-profile.yaml` | NVIDIA GPU, RGB-D camera | Optional and GPU-gated |

| Profile | Collision chassis | LiDAR | Bumper |
| --- | --- | --- | --- |
| Default | 0.22 m radius, 0.089 m tall | Full circle at the existing default rate | Optional |
| Compact example | 0.22 m radius, 0.06 m tall | 270° at 8 Hz, 360 rays, 0.005 m Gaussian range noise | Enabled |

The compact model keeps the stock wheelbase, footprint radius, and LiDAR
mounting frame. Its chassis geometry is lower, so it remains compatible with
the current CPU reachability checks and Nav2 parameters. The compact profile
illustrates the allowed LiDAR field of view, scan rate, sample count and range
noise settings. The [sensor guide](phase-3-sensors.md) describes the optional
observation flags.

```sh
envfuzz baseline examples/scenario.yaml --n 1 \
  --profile examples/compact-vacuum-profile.yaml --output runs/compact-baseline
envfuzz run examples/scenario.yaml --profile examples/compact-vacuum-profile.yaml \
  --baseline runs/compact-baseline/baseline.json --output runs/compact
envfuzz replay runs/compact --output runs/compact-replay
```

Use the organizer's intended repeat count for published calibration. The
one-trial example is a smoke check. A scoring baseline must match the
mission, Nav2 settings and simulation profile. Existing bundles and
baselines retain their implicit default settings. New bundles store the
resolved settings in `run.json` and archive the supplied profile as
`profile.yaml` for replay.

In a classroom challenge, set `robot: compact_vacuum` and point `profile` at
a matching file inside the challenge directory. The optional
`navigation_backend` defaults to `slam_toolbox` and must agree with the
profile. Use separate baselines when changing robot or sensor settings.

## Experimental AMCL localization profile

`examples/amcl-localization-profile.yaml` selects Nav2 AMCL. EnvFuzz derives a
deterministic occupancy map from the static scenario layout and archives its
PGM image and map-server YAML in the run bundle. AMCL starts Nav2 with that
map instead of `slam_toolbox`. It needs a fresh matching baseline. The
pinned-runtime comparison is in the
[backend comparison record](phase-3-backend-comparison.md).

## Lidar-only RTAB-Map profile

`examples/rtabmap-lidar-profile.yaml` selects `rtabmap_lidar`. This is the CPU
alternative SLAM backend. RTAB-Map runs on the planar `/scan` and wheel
odometry, using ICP registration constrained to three degrees of freedom. It
has no camera and no GPU requirement. RTAB-Map supplies `/map` and the
`map` → `odom` transform, and Nav2 starts without SLAM Toolbox, AMCL or a map
server. The profile cannot enable `camera_profile`, and it needs a fresh
matching baseline. Live verification in the pinned runtime is pending.

## Optional RTAB-Map camera profile

`examples/rtabmap-camera-profile.yaml` selects RTAB-Map with a 320 x 240 RGB-D
camera at 15 Hz. The profile is optional and requires an NVIDIA GPU exposed to
the runtime; EnvFuzz checks `nvidia-smi` before it launches Gazebo. It starts
the Gazebo RGB-D camera, bridges its image, depth-image and camera-info topics,
and starts RTAB-Map without visual tools. The bundle records the resolved
camera dimensions, rate, and `rtabmap` backend.

Run it only in a GPU-enabled image runtime:

```sh
docker build -t envfuzz:camera .
docker run --rm --gpus all --network none -v "$PWD/runs:/runs" envfuzz:camera \
  run examples/scenario.yaml --profile examples/rtabmap-camera-profile.yaml --output /runs/rtabmap
```

Use a fresh baseline made with this same profile before scoring. The default
profile remains CPU-only and uses `slam_toolbox`; it does not load the camera
or RTAB-Map path.

## Headless smoke results

Both profiles completed one calibration trial and a separate scored episode
with the resulting baseline. The default profile scored zero. The compact
profile completed its goal and recorded a T3 map event in the scored run.
A one-trial calibration checks wiring and compatibility. Use a repeated
calibration before comparing mapping behavior.
