# Design

## Context

Phase 1 verified headless ROS 2 Jazzy and Gazebo Harmonic with Mesa software rendering. Phase 2 added a sim-time timeline, isolated workers, a campaign index, and an authoritative classroom evaluator. Its replay pilot matched 1 of 17 valid runs. ADR 0004 also records that the tested fast profiles missed the 5× real-time target and failed navigation.

## Goals / Non-Goals

**Goals**

- Preserve the CPU-only default, current scenario inputs, bundle readers, and trusted evaluation entry point.
- Make profile selection and verification evidence portable across hosts.
- Admit regression fixtures to required CI based on measured replay stability.

**Non-Goals**

- Live sensor injection, a participant-controlled spoofing emitter, and porting the legacy spoofer.
- Making GPU resources mandatory for core evaluation or CI.

## Decisions

1. **Version and resolve profiles before execution.** Use the existing scenario, runtime profile, and bundle contracts. Resolve data-only robot and backend identifiers against trusted configurations. Bind calibration to the resolved robot, backend, sensor, and timing settings so comparisons use compatible evidence. The optional camera profile uses RTAB-Map's RGB-D launch path, a Gazebo depth camera, and an explicit GPU capability check. It is selected only with the `rtabmap` backend and never replaces the CPU-only `slam_toolbox` default. Pin the RTAB-Map package in the runtime and document its installed package, GPU host, CPU cost, and integration result before marking it supported.
   Map-based localization uses Nav2 AMCL with a deterministic occupancy-map export made from the scenario's static layout. The export is archived in each AMCL bundle and supplies both the `map_server` input and the identity of the calibration evidence.
   The CPU alternative SLAM backend for the three-backend comparison is the lidar-only `rtabmap_lidar` backend, which a profile selects. It runs RTAB-Map on the planar LiDAR scan and wheel odometry with no camera and no GPU requirement, and it needs its own matching baseline. The RGB-D camera profile remains GPU-gated and can only be selected with `rtabmap`.
2. **Reuse the simulation clock and observation contracts.** Extend the compiler for declarative materials and floor physics. Reuse the Phase 2 timeline for scripted actors and capture named sensor observations in the existing telemetry flow. For surface materials, use Gazebo's native `laser_retro` and visual transparency fields where supported, and keep navigation on the native `/scan` output. Treat the offline material reference model as the normative check for transmission, reflectivity-dependent dropouts, and thin-object ray intersections. The reference model is deterministic test evidence, not live sensor physics; do not add a custom scan filter or Gazebo sensor plugin for this change. Preserve the Phase 1 LiDAR interface.
3. **Keep defensive sensor-fault traces offline.** Store fixed, versioned fixtures under organizer control and exercise the existing oracle against them. Validate submitted data and verify network, mount, and resource isolation using the current worker boundary.
4. **Extend the existing evaluator to multiple hosts.** Give trusted workers versioned job manifests and bounded claims. Keep worker attempts immutable and let the organizer reconcile the authoritative result. Preserve single-host execution as the default and exercise failure recovery with fake workers before a live two-host test.
5. **Measure replay before enforcing it.** Freeze fixture, version, profile, and resource settings. Define agreement and performance thresholds before benchmarking. Gate required CI on proven fixtures and track the Phase 2 95% agreement and 5× speed objectives as separate measurements. Evaluate additional coverage sampling only after a stable reference run is available.
6. **Build releases from verified profiles.** Use CPU-only CI for required checks, architecture-specific smoke checks for image releases, and a separate optional camera check. Document the host, memory budget, and native or emulated architecture for each smoke result.

## Risks / Trade-offs

- Timing variation can dominate backend and search comparisons. Report per-profile agreement, retries, and compute cost and keep unstable cases diagnostic.
- New simulated surfaces and sensors can produce plausible-looking but unvalidated observations. Publish fixture tolerances and CPU costs alongside each supported profile.
- Worker restarts can create duplicate attempts. Preserve attempt provenance and commit one authoritative result per job.

## Migration Plan

Introduce versioned profile fields with defaults matching current scenarios. Verify the existing CLI, scenario, and bundle paths before enabling new profiles. Roll out multi-host execution after a two-host acceptance run and retain the single-host execution path for rollback. Publish supported profiles with versioned images after the release checks pass.
