## Context

The repository contains only a backlog and OpenSpec configuration. The target workflow crosses Python orchestration, ROS 2/Gazebo processes, generated SDF, baseline metrics, and durable run artifacts. It must remain usable on a GPU-less laptop and must distinguish simulation-time behavior from host performance.

## Goals / Non-Goals

**Goals:**

- Make one episode self-contained and reproducible from a scenario and seed.
- Keep scenario, validity, compilation, execution, oracle, and fuzzing concerns separable so later parallel execution and richer search strategies can build on them.
- Make failures and scores inspectable from files without requiring a live simulator.
- Establish test seams for pure logic and a minimal integration path for the real stack.

**Non-Goals:**

- Parallel scheduling, runtime world events, map-error detection, sensor spoofing, minimization, or CTF submission/evaluation.
- GPU acceleration, graphical tools, or support for arbitrary robot platforms.
- Replacing Nav2 or `slam_toolbox` with custom navigation or SLAM.

## Decisions

1. **Use a single process tree per episode, with container isolation as the distribution boundary.**
   The Python runner will own launch, goal submission, sampling, termination, and cleanup. A container entrypoint can later provide stronger isolation without changing the episode contract. A shared long-running simulator was rejected because leaked state and process cleanup make reproducibility and retries harder.

2. **Keep the scenario model pure and canonical before invoking ROS tooling.**
   Pydantic-backed models will load YAML, validate fields, serialize with stable ordering, and provide the hash input. The compiler will consume this canonical model and emit deterministic SDF. This separates fast unit tests from simulator-dependent tests and avoids using simulator parsing as scenario validation.

3. **Use an explicit adapter boundary for ROS/Gazebo observation and control.**
   The runner and oracle will consume normalized samples (poses, mission state, timestamps, zones, and runtime metrics), while ROS-side nodes or bridges provide those samples. This permits synthetic detector traces and keeps frame alignment logic documented and testable.

4. **Treat the unfuzzed baseline as a required versioned input to scoring.**
   Calibration will persist distributions and threshold derivation metadata. Run results will record the baseline identifier and configuration used, preventing a score from appearing valid when its comparison data is missing or incompatible.

5. **Make the CPU rendering choice an evidence-driven ADR before finalizing the image.**
   The P1-02 spike will measure Gazebo LiDAR under Mesa/llvmpipe at the required ray count and real-time factor. If that path is not viable, the design will select a CPU ray-casting plugin or explicitly document a lightweight fallback while preserving the same sensor topic contract. The decision will not be hidden in application code.

6. **Use append-only JSONL for Phase 1 fuzz findings and per-run directories for detail.**
   JSONL is easy to stream, recover, diff, and inspect while the system is sequential. A later SQLite results database can consume the same versioned result schema without changing episode behavior.

7. **Pin runtime inputs and record versions in every bundle.**
   Docker, apt, and Python dependency versions will be pinned where practical; diagnostics will capture exact runtime versions and launch configuration. This trades some update convenience for reproducibility.

## Risks / Trade-offs

- **[Risk] CPU-only Gazebo LiDAR may be too slow or unavailable with software rendering.** → Measure it first, record the result in ADR 0003, and keep the topic contract stable so a tested CPU ray-casting fallback can replace rendering if needed.
- **[Risk] SLAM map and world frames have an arbitrary origin.** → Capture an explicit frame alignment transform during the baseline/run and apply it before pose-error evaluation; cover the transform with synthetic tests.
- **[Risk] Process cleanup may fail during crashes or Ctrl-C.** → Use a dedicated process group, signal handling, watchdog ownership, and a final cleanup path; integration tests will check for surviving episode processes.
- **[Risk] Baseline variability can create false T1/T2 findings.** → Calibrate repeated baseline distributions, expose the threshold formula and floor in `baseline.json`, and require the baseline to be associated with the same world/mission configuration.
- **[Risk] Full ROS integration tests are expensive on developer machines.** → Keep schema/compiler/validity/detector tests simulator-free and mark one short container test as integration-only, with `make test` and `make test-integration` separated.
- **[Risk] Large images and pinned ROS dependencies increase build time.** → Use a dedicated CPU image with no GUI/NVIDIA packages, document measured size/build time, and avoid pulling unused desktop dependencies.

## Migration Plan

1. Bootstrap the repository and create the pure scenario, oracle, and result contracts.
2. Build and measure the CPU-only simulation image and record the LiDAR decision.
3. Add baseline world/robot bring-up, then connect the runner and result bundle.
4. Add calibration, scoring, and seeded fuzzing on top of the runner.
5. Run unit tests, the opt-in integration smoke test, and the documented quickstart.

Rollback is a source-level revert before adoption; Phase 1 introduces no existing data migration. Run directories are disposable and must not be treated as compatible if their result schema version differs.

## Open Questions

- The exact Gazebo Harmonic service names and message types for future runtime world edits are intentionally deferred to Phase 2 and do not affect the Phase 1 static-placement contract.
