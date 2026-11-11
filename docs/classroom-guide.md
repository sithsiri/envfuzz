# Classroom benchmark guide

## Participant workflow

Start with the published challenge directory. Its manifest identifies the
scenario, robot, profile, calibration and allowed scene edits. Submit a
single JSON file with the challenge ID, team name and edits. The checked-in
reference submission shows the format.

```sh
fixture=examples/classroom/multi-room
envfuzz classroom check "$fixture/challenge.yaml" "$fixture/reference.json"
```

The check validates the schema, edit budget, geometry, clearance and route
reachability. Use the same manifest when checking your own submission.
Run locally with the supplied scenario and baseline to inspect diagnostic
tiers and evidence. The organizer verifies scores by running all repeats
on the published profile. The scoreboard ranks verified submissions by
completed missions, then by median successful simulated duration.

## Organizer workflow

1. Select the world and mission, allowed edits, robot and simulation
   profile. Keep scenario, baseline, and configuration files beside the
   challenge manifest or in its subdirectories. See [supported profiles](phase-3-profiles.md).
2. Calibrate the unfuzzed mission with the selected profile using
   `envfuzz baseline`. Keep the runtime image, calibration, profile, limits
   and oracle configuration fixed across submissions to the same version.
3. Check that an organizer-owned reference submission can finish the
   mission. Use the repeat count and resource limits published in the manifest.
4. Rerun each valid submission with the organizer's pinned Docker image.
   Keep the result bundle and failed-attempt diagnostics for review.

```sh
fixture=examples/classroom/multi-room
envfuzz classroom evaluate "$fixture/challenge.yaml" "$fixture/reference.json" \
  --image envfuzz:jazzy --output runs/reference-evaluation
envfuzz classroom scoreboard runs/reference-evaluation/evaluation.json \
  --output runs/reference-scoreboard.json
```

Verify that `evaluation.json` has the intended manifest and baseline
digests and that `verified` is true. Use the verification output as input
to the scoreboard. The evaluator uses a private network namespace with
networking disabled, read-only inputs, a read-only root, and a separate
output mount. It applies the manifest's CPU, memory and watchdog limits.
Review infrastructure failures separately from navigation outcomes.
Organizer-owned sensor fixtures can be checked with
`envfuzz classroom check-sensor-fixtures`.

## Extending the benchmark

- **Detectors** use the trusted `envfuzz.detectors` entry point documented
  in the [Phase 2 workflow](phase-2.md). Add a synthetic trace with a
  known expected event and score before enabling a detector in a challenge.
- **Scene edits** extend the validated operators in `variants.py` and the
  declared edit budget. Keep seed use explicit and test that invalid
  geometry or an exceeded budget is rejected.
- **Sampling strategies** extend the seeded generators in `benchmark.py`.
  Compare coverage and runtime at equal budgets on the same saved fixtures.
- **Sensors** use a versioned profile flag, a simulator-side declaration
  and a read-only bridge into recorded telemetry. Verify timestamps and
  test the observation against a fixed fixture. See the [sensor guide](phase-3-sensors.md).
