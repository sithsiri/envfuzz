## 1. Repository and contracts

- [x] 1.1 Bootstrap the repository layout, Apache-2.0 license, `AGENTS.md`, `.gitignore`, `pyproject.toml`, and Makefile targets; verify `make test` and `make lint` pass in a clean checkout.
- [x] 1.2 Record ADR 0001 for decisions D1-D6 and the O3DE replacement, plus ADR 0002 identifying `sithsiri/LiDARSpoofing` as legacy reference-only; verify both ADRs cite the applicable backlog decisions.
- [x] 1.3 Define the versioned scenario models, YAML loader, canonical serializer, and exported JSON Schema; verify valid, invalid, round-trip, and stable-hash tests pass and `envfuzz scenario validate` reports field-specific errors.
- [x] 1.4 Define the normalized episode sample, result, event, baseline, and version metadata schemas; verify schema validation rejects incomplete result bundles and accepts documented examples.

## 2. CPU-only simulation foundation

- [ ] 2.1 Create the pinned CPU-only Ubuntu 24.04 / ROS 2 Jazzy / Gazebo Harmonic image with `ros_gz`, Nav2, SLAM, `nav2_minimal_tb3_sim`, and the envfuzz package; verify `docker build` succeeds without NVIDIA or X11 dependencies.
- [ ] 2.2 Measure Gazebo LiDAR with Mesa software rendering at 360 rays and record scans/second, CPU usage, and real-time factor in ADR 0003; verify the recommendation and material-effect implications are explicit.
- [ ] 2.3 Implement the selected LiDAR path while preserving the documented scan topic contract; verify the sensor publishes in the headless image with DISPLAY unset.
- [ ] 2.4 Build the apartment SDF world, circular differential-drive robot, sensors, odometry, and ground-truth pose publisher from redistributable primitives/assets; verify the asset license record and required topics are present.
- [ ] 2.5 Add headless bring-up for Gazebo, `/clock`, sensor/control bridges, Nav2, and `slam_toolbox` with `use_sim_time`; verify the unfuzzed robot reaches a default goal without a display.
- [x] 2.6 Add `envfuzz doctor` and runtime version capture; verify it prints all pinned component versions both in the image and in a sample run bundle.

## 3. Scenario compilation and validity

- [x] 3.1 Implement deterministic static scenario-to-SDF compilation for supported boxes and cylinders; verify repeated compilation produces byte-identical SDF and golden-file tests pass.
- [x] 3.2 Implement world-bound, overlap, wall-intersection, start/goal-clearance, and restricted-zone validity rules; verify each rule returns a specific violation and valid scenarios pass.
- [x] 3.3 Wire validation and compilation into the CLI and reject unsupported dynamic events; verify invalid scenarios never launch a simulator.

## 4. Episode runner and outputs

- [ ] 4.1 Implement the single-episode process-tree runner with scenario validation, world compilation, launch, goal submission, and normalized observation collection; verify a valid scenario completes the core loop.
- [x] 4.2 Implement sim-time timeout, configurable wall-clock watchdog, exit-code documentation, and infrastructure-versus-behavior failure classification; verify goal completion, timeout, launch failure, and watchdog cases produce distinct statuses.
- [x] 4.3 Implement signal-safe process-group cleanup for normal exit, crash, and Ctrl-C; verify an interrupted test run leaves no episode-owned child processes.
- [x] 4.4 Implement the versioned result bundle containing scenario, world, result, versions, timings, achieved real-time factor, logs, and optional rosbag; verify required files are present even after a simulator failure.

## 5. Baseline and oracle

- [ ] 5.1 Implement repeated unfuzzed baseline calibration and explainable threshold derivation with a baseline/world/mission identity; verify `envfuzz baseline --n 10` writes distributions and thresholds.
- [x] 5.2 Implement explicit SLAM-to-world frame alignment and normalized pose recording; verify synthetic transforms and different map origins produce correct aligned errors.
- [x] 5.3 Implement the common detector interface and T1 stuck detector with baseline-completion gating; verify synthetic traces cover positive, negative, and boundary cases.
- [x] 5.4 Implement the T2 pose-error and T4 restricted-area detectors; verify synthetic traces cover duration thresholds, polygon entry, event intervals, and no-event cases.
- [x] 5.5 Implement configurable tier scoring and baseline-required run/fuzz behavior; verify all events are retained and the configured highest-tier score and tiers-hit output are correct.

## 6. Seeded fuzzing

- [x] 6.1 Implement seeded random generation of supported objects and placements with deterministic candidate ordering; verify repeated runs with the same seed generate equivalent candidates.
- [x] 6.2 Implement rejection and retry accounting so invalid candidates do not consume the valid simulation budget; verify rejected candidates are diagnosable and budget counts only executed scenarios.
- [x] 6.3 Implement sequential `envfuzz fuzz --budget N --seed S`, append-only `runs/results.jsonl`, and descending score/tier summary output; verify a small deterministic fuzz run persists every completed result.

## 7. Verification and documentation

- [x] 7.1 Add simulator-free unit coverage for schema, compiler, validity, result contracts, and detectors; verify `make test` completes in under 30 seconds without a simulator.
- [ ] 7.2 Add the marked short container integration smoke test and `make test-integration`; verify it runs one tiny-world episode and checks the result bundle.
- [x] 7.3 Write the CPU-only quickstart, CLI reference, exit statuses, threshold configuration, image size/build-time measurements, and troubleshooting guidance; verify a fresh checkout can follow the documented `fuzz --budget 20` flow.
- [ ] 7.4 Run the full Phase 1 acceptance checklist and record any environment-specific limitations; verify the critical path and all Phase 1 smoke tests pass.
