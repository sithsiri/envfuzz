## Why

The repository is currently planning-only, so there is no runnable path from an environment scenario to a measured autonomous-robot outcome. Phase 1 establishes the smallest reproducible, CPU-only workflow that lets users generate scenarios, run a headless episode, detect meaningful deviations from an unfuzzed baseline, and inspect findings.

## What Changes

- Bootstrap the Python project, repository conventions, ADRs, tests, and reproducible development commands.
- Provide a pinned, headless Ubuntu 24.04 / ROS 2 Jazzy / Gazebo Harmonic CPU image with the baseline apartment world and differential-drive robot.
- Add a versioned scenario format with validation, canonical serialization, deterministic world compilation, and physical/logical validity checks.
- Add a single-episode runner with sim-time termination, a configurable wall-clock watchdog, process cleanup, failure classification, and versioned result bundles.
- Add baseline calibration and an oracle for stuck behavior, SLAM pose error, and restricted-area entry, using baseline-derived thresholds and synthetic unit tests.
- Add deterministic random scenario generation and sequential fuzzing with budget accounting, JSONL result output, and a sorted findings summary.
- Add unit and integration smoke tests plus quickstart and operational documentation.

## Capabilities

### New Capabilities

- `scenario-management`: Versioned scenario loading, validation, canonical hashing, deterministic SDF compilation, and validity checking.
- `headless-simulation`: CPU-only containerized baseline world, robot, sensor/odometry/ground-truth topics, Nav2, SLAM, and sim-time launch.
- `episode-execution`: Single-episode lifecycle, watchdog and termination behavior, cleanup, infrastructure failure classification, and result bundles.
- `oracle-scoring`: Baseline calibration, ground-truth/SLAM recording, T1/T2/T4 detectors, threshold configuration, and tiered scoring.
- `random-fuzzing`: Seeded valid-scenario generation, sequential budget execution, reproducible result persistence, and findings summaries.

### Modified Capabilities

- None.

## Impact

- Adds the initial Python package under `src/envfuzz`, ROS 2 packages under `ros2_ws/src`, Docker and build tooling, scenario/world assets, documentation, ADRs, and tests.
- Introduces CLI commands including `envfuzz scenario validate`, `envfuzz baseline`, `envfuzz run`, and `envfuzz fuzz`.
- Adds runtime dependencies on Python tooling, Pydantic, Typer, Pytest, ROS 2 Jazzy, Gazebo Harmonic, `ros_gz`, Nav2, `slam_toolbox`, and `nav2_minimal_tb3_sim`.
- Creates gitignored run outputs under `runs/`; no existing production API or persisted data must remain compatible because the repository has no implementation yet.
