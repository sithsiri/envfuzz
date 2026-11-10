# Phase 1 verification

## Runtime

The CPU image built for Linux ARM64 and AMD64 on Docker Desktop with eight VM
CPUs. Both use Ubuntu 24.04, ROS 2 Jazzy, and Gazebo Sim 8.15.0. The container
received no GPU or display mounts, and `DISPLAY` was unset during the runs.

| Image | Build wall time | Unpacked image size |
| --- | ---: | ---: |
| Linux ARM64 | 3 min 47 s | 4.80 GB |
| Linux AMD64 | 6 min 48 s | 5.13 GB |

These measurements include the package fetch and image export. The AMD64
build and execution used emulation on Apple Silicon.

## Episode checks

- The ARM64 image passed `make test-integration` and recorded motion,
  timestamped ground-truth poses, aligned SLAM estimates, and the result bundle.
- The AMD64 image passed the integration test on rerun after an initial
  navigation failure under emulation. The packaged CLI also completed a
  separate example in 4.55 simulated seconds with 45 paired pose samples.
- A short mission limit produced `timeout` with a behavior classification.
  A short wall watchdog produced `watchdog` with an infrastructure
  classification. Sending Ctrl-C persisted an `interrupted` result and
  stopped the episode processes.
- The 360-ray headless LiDAR measurement and CPU usage are recorded in
  [ADR 0003](adr/0003-cpu-lidar.md). The [asset and interface record](simulation-assets.md)
  lists the robot source and the ROS topics verified during the probe.

## Calibration

Ten default-mission trials completed in the ROS 2 Jazzy development
container. Simulated durations ranged from 4.555 to 6.058 seconds. The
0.10 m floor controlled the calibrated position-error threshold. The baseline
retains both measured distributions and the world, mission, and Nav2
identifiers.

## Seeded fuzz acceptance

The packaged ARM64 CLI ran seed 42 for 20 scored episodes. All 20 reached
their goal and scored zero. One startup watchdog was retried successfully,
and zero candidate placements were rejected. Each JSONL result matched its
bundle, baseline digest, and expected position in the seeded scenario
sequence. The run data is available locally under
`runs/phase-1-verification/`.

The final source passed 18 simulator-free tests, and the headless integration
test passed in both packaged architectures.
