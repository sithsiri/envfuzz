# Phase 2 navigation benchmark

## Why

Phase 1 established a verified headless simulator, scored runs, and a resumable sequential campaign. Phase 2 makes the benchmark practical for larger experiments and gives the class a reproducible way to evaluate submissions against shared navigation and mapping criteria.

## What Changes

- Run independent episodes concurrently with resource limits and persistent retry accounting. Index results in SQLite and keep per-episode bundles available for replay.
- Generate ground-truth occupancy grids, use footprint-aware reachability checks, and score mapping errors alongside existing telemetry with an explicit oracle configuration.
- Add seeded environment mutations and scheduled changes in simulation time. Compare random, stratified, and coverage-guided sampling under equal budgets.
- Preserve and simplify navigation and mapping regressions. Measure replay agreement and CPU throughput across controlled profiles.
- Package classroom scenarios and evaluation rules as data. Validate submissions, rerun them in an isolated organizer environment, and publish verified benchmark results.

## Capabilities

### New Capabilities

- `parallel-campaigns` — Durable worker scheduling and indexed campaign results.
- `map-evaluation` — Ground-truth maps, reachability, configurable detectors, and diagnostic plots.
- `scenario-variants` — Validated seeded edits and scheduled environment changes.
- `benchmark-reproduction` — Sampling comparisons, a regression corpus, simplification, and replay stability.
- `classroom-evaluation` — Versioned benchmark packages, data-only submissions, and organizer-verified results.

### Modified Capabilities

## Impact

The work extends the current campaign, compiler, ROS launch, scoring, and report paths. The open `verify-navigation-benchmark` change supplies the pilot analysis and replay evidence. Phase 2 consumes that output and tracks the larger experiment and runner work separately.
