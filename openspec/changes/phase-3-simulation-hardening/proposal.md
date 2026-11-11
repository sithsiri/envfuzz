# Phase 3 simulation hardening

## Why

The project can run and score headless episodes across isolated workers. Phase 3 broadens the simulated conditions and robot configurations the benchmark can evaluate, then makes those evaluations reproducible and maintainable across hosts and releases.

## What Changes

- Add organizer-owned, fixed synthetic sensor-fault traces for defensive robustness tests. Strengthen data-only submission validation and verify sensor-channel isolation during evaluation.
- Add validated simulation models for indoor surface response, contact and cliff sensors, floor friction and odometry, and scripted actors. Keep camera-based evaluation optional and GPU-gated.
- Make robot and navigation configurations selectable and record them in matching baselines, results, replays, and comparison reports.
- Run the existing isolated evaluator on multiple hosts with durable job recovery and a single authoritative result per evaluation.
- Measure replay reliability before promoting fixtures to regression gates. Evaluate optional coverage-driven sampling under equal simulation budgets.
- Add CPU integration, security and property tests, versioned multi-architecture releases, cross-platform smoke checks, and participant and organizer guides.

## Capabilities

### New Capabilities

- `simulation-profiles` — Versioned sensor, robot, and navigation profiles with matched baselines and a CPU-only default.
- `release-verification` — CI, reproducible releases, and supported runtime profiles.

### Modified Capabilities

- `scenario-variants` — Surface properties, floor patches, and deterministic moving actors.
- `parallel-campaigns` — Durable scheduling and result reconciliation across hosts.
- `benchmark-reproduction` — Evidence-based replay gates and comparative coverage experiments.
- `classroom-evaluation` — Defensive sensor-fault fixtures, submission isolation, and operator documentation.

## Impact

Extends the scenario compiler and ROS launch, campaign workers, result metadata, classroom evaluator, and test and release infrastructure. Existing scenarios and bundle readers continue to work with the current defaults. P3-01 is scoped to fixed defensive fixtures and isolation checks. The backlog's participant-controlled emitter and legacy spoofer port are outside this proposal.
