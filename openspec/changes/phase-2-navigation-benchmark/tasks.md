# Tasks

## 1. Parallel runs and storage

- [ ] 1.1 Implement the persistent worker queue and per-episode container limits (P2-01). Verify worker isolation, cleanup, infrastructure retries, and resume. Record throughput at 1, 2, 4, and 8 workers.
- [ ] 1.2 Index attempts in SQLite and add the campaign HTML report (P2-02). Verify filters by score, tier, scenario hash, and strategy against saved bundles.

## 2. Mapping and evaluation

- [ ] 2.1 Generate reference occupancy grids and align final SLAM maps (P2-03). Verify the chosen metric on synthetic maps with changed origin, rotation, and unknown cells. Calibrate its T3 threshold on repeated baselines.
- [ ] 2.2 Add configurable footprint-inflated A* reachability (P2-04). Verify clear, narrow, and blocked waypoint routes.
- [ ] 2.3 Load the oracle configuration and detector interface (P2-05). Verify configured tier names, points, thresholds, timestamped evidence, and a recovery-loop trace with a synthetic fixture.
- [ ] 2.4 Add CPU-rendered `envfuzz show` output for maps, poses, and events (P2-14). Verify a saved run can generate the report offline.

## 3. Scenario edits

- [ ] 3.1 Implement seeded object, clutter, and door edits with budget checks and a reserved surface-friction field (P2-06). Verify repeatability and valid geometry with property-based tests.
- [ ] 3.2 Add versioned timed edits and preserve Phase 1 scenario loading (P2-07). Verify an integration fixture moves an object at the scheduled simulation step and changes subsequent LiDAR samples.

## 4. Reproduction and sampling

- [ ] 4.1 Evaluate throttled and lock-step replay (P2-10). Record agreement and real-time factors across CPU profiles in an ADR and verify the archived bundle can be replayed.
- [ ] 4.2 Profile physics, LiDAR, Nav2, and SLAM settings (P2-11). Report the best measured CPU real-time factor and its replay agreement against the initial targets.
- [ ] 4.3 Use the comparison method from `verify-navigation-benchmark` to evaluate random, stratified, and coverage-guided sampling (P2-08). Verify equal budgets and publish coverage and navigation outcomes with a deduplicated regression corpus.
- [ ] 4.4 Add `envfuzz minimize` for saved navigation and mapping regressions (P2-09). Verify a reduced fixture retains its documented reproduction rate across the prescribed reruns.

## 5. Classroom evaluation

- [ ] 5.1 Define the benchmark manifest and data-only submission format (P2-12). Verify the local checker rejects excess file sizes, invalid geometry, and edits outside the allowed budget.
- [ ] 5.2 Implement organizer evaluation and scoreboard export (P2-13). Verify resource limits, network isolation, read-only inputs, and the repeat policy in an integration test.
- [ ] 5.3 Publish three calibrated layout families with documented difficulty and organizer reference fixtures (P2-15). Verify each fixture can be validated and evaluated from its manifest.
