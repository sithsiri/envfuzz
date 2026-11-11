# EnvFuzz project brief for advisor feedback

**Prepared November 11, 2026**  
**Purpose:** summarize implementation phases and preliminary experimental evidence; request guidance on the next research question.

## Project summary

EnvFuzz is a ROS 2 Jazzy / Gazebo Harmonic framework for testing indoor robot navigation under controlled changes to the simulated environment. It provides scenario generation, repeatable missions, Nav2 integration, telemetry, oracles, replay, and parallel Docker workers. The project aims to fuzz capabilities of existing navigation code, so experiments need to configure the capability under study and use outcomes that distinguish expected behavior from a regression.

## Phase status

| Phase | Status in project checklist | Scope and evidence |
|---|---:|---|
| **1 — MVP** | 29/29 tasks checked | CPU-only headless simulation, deterministic scenario compilation, calibration, scoring, seeded obstacle fuzzing, and durable run bundles. The Phase 1 seed-42 experiment completed 20/20 goals, with zero scored events. |
| **2 — Navigation benchmark** | 15/15 tasks checked | Parallel campaigns, uniform/stratified/coverage-guided sampling, map diagnostics, timed edits, replay/minimization, reports, and classroom evaluation. An eight-worker benchmark reached 9.68 behavior episodes/min on an eight-CPU VM. |
| **3 — Simulation hardening** | 11/20 tasks checked | Scripted actors, material and sensor fixtures, motion/contact/cliff observations, robot profiles, AMCL, and classroom guidance. The remaining checklist includes replay/coverage acceptance, alternative-backend comparison, GPU camera verification, and release work. |

The project status and detailed records are in [progress](progress.md), [Phase 1 verification](phase-1-verification.md), [Phase 2 verification](phase-2-verification.md), and [Phase 3 verification](phase-3-verification.md).

## Preliminary experiments

### Initial restricted-zone fuzzing

The initial experiment was reported as 100 runs and looked null because Nav2 crossed a restricted zone. Inspection of the open `trial-0007` result found a T4 `restricted_area` event but `score: 0` and no baseline hash. More generally, the original run configuration had no Nav2 Keepout Filter enabled: the zone was represented in EnvFuzz's oracle, but not supplied to Nav2 as a costmap constraint. Nav2 Jazzy includes a [Keepout Filter](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/costmap_2d/costmap_filters/keepout_filter/); it must be configured on the relevant costmaps and paired with an aligned filter mask. The default parameters do not enable it.

### Keepout Filter controlled pilot

We added an opt-in Keepout Filter path, generated a mask aligned to the AMCL map, and enabled footprint inflation. In five matched control/filter pairs on the same start, goal, and zone:

| Condition | Restricted-zone entries | Goal completion |
|---|---:|---:|
| Filter disabled | 5/5 | 5/5 |
| Filter + inflation enabled | 0/5 | 5/5 |

The first filtered run showed why footprint inflation matters: the robot center detoured around the zone while its footprint still clipped the boundary. After adding inflation, the event disappeared. These runs were not baseline-scored; the result is a direct comparison of recorded zone-entry events, not a comparison of score values.

### Dynamic obstacles

Three repeats each compared a stationary off-route obstacle with a scripted actor crossing the route and a timed obstacle move crossing the route. All 12 runs reached the goal and recorded zero bumper contacts. All actor updates (100 per run) and all three timed moves per run were applied. Median path lengths were:

| Condition | Median path |
|---|---:|
| Stationary control | 3.77 m |
| Scripted actor | 4.68 m |
| Timed object move | 4.76 m |

Both timeline mechanisms changed navigation measurably without preventing goal completion in this fixture.

### LiDAR degradation

Three runs per condition used a fixed obstacle course with default LiDAR versus a 120° field of view and 2 cm range noise. All six runs reached the goal. Median path length was 4.83 m nominal and 4.89 m degraded; median position RMSE was 0.013 m in both conditions. This mild degradation did not produce a meaningful regression in the tested course.

Pilot procedures and run bundles are indexed in the [Phase 2 workflow](phase-2.md); the new runs are under `runs/keepout-pilot-repeats-20261111/`, `runs/dynamic-obstacle-pilot-20261111/`, and `runs/lidar-degradation-pilot-20261111/`.

## Evidence limits and open issues

- The new pilots use one simple map and a small number of repeats. They are feasibility checks, not broad or statistically powered claims.
- The mild LiDAR profile change may be too weak to expose a failure; more severe profiles and varied geometry remain to be tested.
- Historical failure replay is unstable: in the Phase 3 diagnostic rerun, **0 of 17 valid replays** matched the expected signatures. This blocks treating current fuzz-generated cases as reliable regression fixtures.
- Some map-error thresholds are poorly discriminating: the three-run empty-room baseline reached the maximum map-error threshold of 1.0. AMCL map-error events were also identified as alignment artifacts in the backend comparison.
- The Phase 3 record reports 58 simulator-free tests passing; live integration tests are separate and require the ROS/Gazebo runtime.

## Questions for advisor guidance

1. Which research claim should the next study target: dynamic-obstacle avoidance, sensor robustness, mapping/localization quality, or reproducibility of navigation failures?
2. What outcome and baseline would make a failure scientifically meaningful—for example, collision/zone-entry rate, mission completion, path cost, or replay agreement?
3. Should the next campaign prioritize broader scenario coverage or repeated trials on a smaller set of carefully controlled cases?
4. What repeat count and statistical analysis would be appropriate before presenting the Keepout Filter or dynamic-obstacle results as evidence?

**Suggested next step:** agree on one primary claim and its oracle with the advisor, then build a matched campaign across several geometries and obstacle timings. Keep infrastructure failures separate from navigation outcomes, preserve every run bundle, and assess replay agreement before admitting discovered cases as regressions.
