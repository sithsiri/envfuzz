# ADR 0001: Phase 1 platform and scoring decisions

## Status

Accepted.

## Decision

Phase 1 uses physical-environment fuzzing (D1), severity-tiered deviation
scoring (D2), CPU-only headless Docker (D3), simulation-time evaluation (D4),
Gazebo Harmonic with ROS 2 Jazzy (D5), and a new repository based on
`nav2_minimal_turtlebot_simulation` and `ros_gz` (D6). O3DE and its
`RobotVacuumSample` are explicitly replaced, not ported.

## Consequences

The pure Python contracts remain simulator-independent, while ROS assets are
optional integration surfaces. Sensor spoofing is deferred to Phase 3.

