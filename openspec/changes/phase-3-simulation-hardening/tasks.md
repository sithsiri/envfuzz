# Tasks

## 1. Establish verification and classroom boundaries

- [x] 1.1 Freeze representative Phase 2 fixtures, versions and host profiles. Repeat the pilot and document agreement, infrastructure failures and real-time factor alongside ADR 0004. Register a required fixture only after it meets its declared replay tolerance. (P3-09)
- [x] 1.2 Add organizer-owned fixed offline sensor-fault traces and test their expected oracle tiers. Document the supported fixture format and organizer controls. (P3-01)
- [x] 1.3 Test rejection of malformed, oversized, deeply nested, out-of-root and unsupported inputs. Verify isolated workers enforce network, mount and resource limits and keep sensor channels inaccessible to submitted data. (P3-01, P3-09)

## 2. Extend the simulated environment

- [ ] 2.1 Add versioned surface properties and budgeted edits. Check transparent, reflective, dark and thin surface fixtures and reflectivity-dependent observation changes against documented synthetic tolerances. (P3-02)
- [x] 2.2 Add contact and bumper observations. Verify a controlled contact emits the expected timestamped event and update the sensor guide. (P3-03)
- [x] 2.3 Add simulated cliff sensing and drop geometry. Verify expected ledge observations in a headless fixture and document the configuration. (P3-03)
- [x] 2.4 Add floor-friction patches and record IMU and wheel-odometry observations. Compare paired runs over a uniform floor and a friction patch and publish the tolerances. (P3-03)
- [ ] 2.5 Add an optional camera and visual SLAM profile with a capability check. Verify the camera profile on a supported GPU host and the default profile on a CPU-only host. (P3-03)
- [x] 2.6 Add validated scripted actor paths to the timeline. Replay a fixture to check simulation timestamps and corresponding LiDAR observations. (P3-04)

## 3. Compare robot and navigation configurations

- [x] 3.1 Resolve allowlisted robot, sensor and backend profiles in challenges and bundles. Test matching and mismatched baselines and legacy scenario defaults. (P3-05, P3-06)
- [ ] 3.2 Add map-based localization and verify an alternative SLAM backend in the pinned runtime. Run a frozen comparison against the default backend and publish outcome and resource measurements. (P3-05)
- [x] 3.3 Add a low-profile vacuum definition and supported LiDAR and bumper parameter variants. Run one calibrated CPU smoke episode per supported profile and document which profiles are available to organizers. (P3-06)
- [ ] 3.4 Evaluate the case for an optional coverage-driven strategy on the stable reference suite. If tested, compare it with Phase 2 methods at equal budgets and record coverage, reproducibility, cost and the adoption decision. (P3-07)

## 4. Extend organizer execution

- [ ] 4.1 Add durable multi-host job claims and bounded retries around the existing evaluator. Verify recovery and authoritative result deduplication with fake workers and a two-host CPU smoke run. Document single-host fallback. (P3-08)
- [x] 4.2 Publish participant, organizer and extension guides covering profile selection, local checks, scoring, calibration, reference verification, evaluation and fairness. Follow the guides to reproduce a reference evaluation. (P3-11)

## 5. Verify releases and platforms

- [ ] 5.1 Expand unit and property coverage for schemas, deterministic compilation, validity, edits, detectors, metrics, scoring and submission validation. Verify the default 85% source coverage target and run the required CPU headless smoke suite. (P3-09)
- [ ] 5.2 Add CI for lint, unit and CPU integration checks and schedule nightly regression and performance runs. Verify failure artifacts, replay tolerance enforcement and benchmark regression reporting. (P3-09, P3-10)
- [ ] 5.3 Produce versioned amd64 and arm64 images, version manifests and release notes through the release workflow. Verify architecture-specific smoke runs before publishing. (P3-10)
- [ ] 5.4 Test the documented Apple Silicon and WSL2 setup and a low-memory run with a declared budget. Record host, memory use, fidelity changes and native or emulated execution. (P3-12)
- [ ] 5.5 Run release acceptance across a legacy scenario, a new CPU profile and an organizer evaluation. Record verification evidence and any remaining gap against the Phase 2 replay and speed targets. (P3-09, P3-10, P3-11, P3-12)

## Pause and owner deferrals

Implementation is paused at the owner's request on November 10, 2026. The 10 completed checkboxes remain the current progress snapshot.

- Task 2.5 remains unchecked. The owner deferred the GPU camera and visual-SLAM work.
- Task 4.1 remains unchecked. The owner deferred distributed execution and two-host verification.
- Task 5.4 remains unchecked. The owner deferred the cross-platform acceptance task, including Windows WSL2 verification.
- Other unchecked tasks remain open. See [progress and evidence](../../../docs/progress.md) for the successes and paused staging work.
