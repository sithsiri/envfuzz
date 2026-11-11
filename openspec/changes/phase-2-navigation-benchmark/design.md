# Design

## Context

Phase 1 already saves portable episode bundles, samples three layout families, checks route connectivity, and renders trajectory SVGs. Its campaign runner is sequential and writes CSV. The benchmark pilot tracked by `verify-navigation-benchmark` defines how to compare episode budgets and count replayable cases.

## Goals

- Scale the same episode contract to multiple workers.
- Make map, navigation, and replay measurements inspectable from saved files.
- Run classroom evaluations against the same fixtures, limits, and repeat policy.

## Decisions

1. **Schedule from a persistent plan.** Keep the frozen scenario order and assign each attempt a separate bundle. Store job state and result indexes in SQLite. Keep Phase 1 CSV and JSONL readable and preserve `report BUNDLE` while adding a campaign report.
2. **Use one spatial reference.** Rasterize static geometry at the configured LiDAR height and preserve grid resolution, origin, and bounds. Inflate by the robot footprint for A* reachability. Compare occupied cells after applying the saved SLAM-to-world transform. Report unknown-cell coverage with occupied-cell IoU to make partial maps visible.
3. **Version scenario and oracle inputs.** Introduce a new schema version for timed edits and keep the current Phase 1 v1 loader. Apply events against the simulation clock and log requested and applied timestamps. Treat the friction operator as reserved data until the Phase 3 surface model can give it defined behavior. Organizer evaluation uses trusted, preinstalled detector code.
4. **Choose sampling by coverage.** Adapt the existing campaign strategies and add coverage-guided selection over layout, clearance, clutter, and mission categories. Use a shared budget and frozen seeds. Deduplicate and simplify observed navigation and mapping regressions for replay.
5. **Measure timing before tuning.** Compare simulation throttling and a lock-step prototype using the same fixture set. Use the backlog defaults of 95% replay agreement and a 5× real-time performance target as initial evaluation criteria. Record measured values and the configuration that produced them.
6. **Separate local checks from organizer results.** Validate the same data-only scenario package locally and on the organizer host. Use fresh evaluation containers, a read-only input mount, a writable result location, and bounded reruns. Publish the verified aggregate with its repeat policy and evidence.

## Risks and trade-offs

- Worker contention can change timing and replay agreement. Measure throughput and outcome agreement together at each worker count.
- Map similarity depends on grid resolution and unknown-cell handling. Publish coverage and test both frame alignment and partial maps with synthetic fixtures.
- Timed edits can change reachability during a mission. Record the initial and scheduled layouts so reports identify the time and geometry associated with an outcome.
- The existing Phase 1 change is complete and still open. It supplies the initial contracts. The pilot analysis remains the source of the comparison methodology used by this phase.
