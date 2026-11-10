# Tasks

## 1. Verify the packaged simulator

- [ ] 1.1 Build the project image on a Docker-capable host and record the image identifier and runtime versions.
- [ ] 1.2 Run the documented example and integration smoke test from the image. Check that Gazebo advances, the robot moves, and the bundle contains real pose telemetry.
- [ ] 1.3 Exercise replay, report, and a short baseline. Check that replay uses the archived parameters, the plot shows both traces, and the baseline contains measured durations.

## 2. Compare campaign results

- [ ] 2.1 Implement analysis over the saved plan and attempt bundles. Test unequal budgets, retries, duplicate signatures, and valid replay counts.
- [ ] 2.2 Document the case definition and analysis command. Verify the documented command against the pilot outputs.
- [ ] 2.3 Run the pilot campaign. Record CPU throughput, confirm all layout families are represented, and freeze the evaluation seeds, budget, and case signature.

## 3. Run the evaluation

- [ ] 3.1 Execute the frozen campaign. Verify both samplers reach the shared behavior-episode budget and account for retries.
- [ ] 3.2 Replay representative cases and produce the outcome table and discovery curves. Trace every counted case back to its original and replay bundles.
