# Map evaluation

## Purpose

Compare simulated maps and trajectories with reference geometry and preserve the evidence needed to diagnose a regression.

## ADDED Requirements

### Requirement: Aligned occupancy comparison

The benchmark SHALL generate a reference grid at LiDAR height and compare it with the final SLAM grid in a shared frame using a documented metric and baseline-derived threshold.

#### Scenario: Map origin differs from world origin
- **WHEN** two equivalent grids have different origins and orientations
- **THEN** the aligned comparison reports equivalent occupancy over comparable cells and records the transform and coverage.

### Requirement: Footprint-aware reachability

The validator SHALL provide a configurable path check from the start through the ordered goals using the reference grid and robot footprint.

#### Scenario: Corridor is narrower than the footprint
- **WHEN** reachability is enabled and the only route has insufficient clearance
- **THEN** validation reports the unreachable waypoint before starting a simulator.

### Requirement: Configurable detector evidence

The oracle SHALL load tier names, points, and thresholds from versioned configuration and record timestamped evidence for each event. Trusted local detector extensions SHALL use a documented interface.

#### Scenario: Repeated recovery loop
- **WHEN** telemetry meets the configured oscillation or recovery criterion
- **THEN** the event records its simulation-time interval and evidence for inspection.

### Requirement: Combined diagnostic plot

The report SHALL show layout geometry, ground-truth and estimated trajectories, event markers, and aligned maps when the corresponding data is available.

#### Scenario: Inspect a mapping regression
- **WHEN** a run has both maps and timestamped poses
- **THEN** a CPU-rendered PNG or HTML view shows map differences and lets the reader locate the recorded events.
