# Phase 2 navigation benchmark

Run these examples from the repository root. Install the host CLI with
`python -m pip install -e '.[test]'` and build the current simulator with
`docker build -t envfuzz:jazzy .`. Host campaign and classroom commands invoke
Docker for each episode.

## Parallel campaigns

```sh
envfuzz campaign examples/phase2-campaign.yaml --workers 2 --output runs/pilot
envfuzz report runs/pilot --strategy uniform
envfuzz analyze runs/pilot
```

Each worker has its own container, ROS domain slot, and Gazebo partition.
`--worker-cpus` and `--worker-memory` set per-container limits. The queue
uses the frozen plan and numbered attempt folders to resume. Infrastructure
failures get up to `--retries` retries. SQLite indexes attempts and tiers.
The report retains links to run bundles, and the CSV remains available to
earlier workflows.

For trusted workers on several hosts, put the frozen campaign output on a
shared POSIX filesystem, create the plan once with `--plan-only`, then run one
command per host with a distinct worker ID:

```sh
envfuzz campaign examples/phase2-campaign.yaml --plan-only --output /shared/pilot
envfuzz campaign-worker examples/phase2-campaign.yaml --output /shared/pilot --worker-id host-a
```

The shared claim ledger assigns a bounded lease to each numbered attempt. An
expired lease can be retried, and the ledger retains every attempt while
selecting one authoritative result for each case. Single-host `campaign`
execution remains available.

The analyzer compares strategies at the common completed episode budget for
each layout. The case signature uses layout, navigation status, map-error
flag, and terminal position rounded to a one-meter world grid. The JSON
output includes coverage, outcomes, discovery curves as counts by episode, and
bundle references. Use `--replays 3` to add replay evidence for each
representative in the regression corpus.

## Maps, scenario edits, and replay

- `envfuzz show BUNDLE` renders trajectory and aligned occupancy evidence as HTML.
- `envfuzz run FILE --baseline FILE --oracle examples/oracle.yaml` enables the
  configured detector settings. Trusted installed detector functions can register
  entry points in the `envfuzz.detectors` group. They receive the scenario,
  bundle, result, baseline, and configuration as keyword arguments and return
  event records.
- `envfuzz run examples/timed-move.yaml` demonstrates a scenario-v2 object move.
  A Gazebo system queues it at the requested simulation step. The bundle records
  the requested and applied timestamps and the surrounding scans. Existing
  static v1 scenarios retain their original hashes.
- `envfuzz replay BUNDLE --profile PROFILE.yaml` can vary the simulation rate,
  physics step, LiDAR frequency, and ray count. Calibrate a matching baseline
  when comparing scores across profiles. The bundle captures the profile used.
- `envfuzz minimize BUNDLE --repeats 3` replays a navigation or mapping regression,
  then tries removing unrelated obstacles. The output retains the case signature,
  selected scenario, and measured repeat rate.

The map metric is occupied-cell IoU over cells known in both grids, after
projecting the SLAM grid into world coordinates. The report also shows coverage,
missing occupancy, and extra occupancy. The T3 detector applies the calibrated
error threshold only above the configured minimum coverage. A threshold that
reaches the metric maximum indicates a baseline too variable for T3 to
distinguish larger errors.

## Classroom evaluation

```sh
fixture=examples/classroom/multi-room
envfuzz classroom check "$fixture/challenge.yaml" "$fixture/reference.json"
envfuzz classroom evaluate "$fixture/challenge.yaml" "$fixture/reference.json" \
  --output runs/evaluation
envfuzz classroom scoreboard runs/evaluation/evaluation.json
```

A submission is one size-limited JSON file of allowed scene edits. The
organizer controls the fixture, baseline, limits, and repeat count. Runs use
a read-only root filesystem and input mount, no networking, a separate
writable result mount, and CPU, memory, and wall-clock limits. The scoreboard
ranks verified submissions by completed missions and then median simulated
completion time. The report retains per-run evidence. Keep organizer scenario,
baseline, and configuration files in the directory containing the challenge
manifest. Manifest file paths are relative to that directory.

## Verification

The [Phase 2 verification record](phase-2-verification.md) includes the
worker-scaling, timing, and classroom measurements.

## Offline sensor observation checks

Organizers can run the checked-in observation suite on the host without ROS or
Docker.

```sh
envfuzz classroom check-sensor-fixtures examples/classroom/sensor-fixtures.json
```

The version 1 JSON format contains a position-error threshold in meters and
fixtures with unique IDs, expected oracle tiers, and ordered observations.
Each observation has a simulation timestamp, ground-truth position, and an
estimated position or null for a missing observation. This fixture format uses
one position axis and the existing T2 pose-error oracle. The report includes
the suite's SHA-256 digest and observed tiers. A mismatch exits with status 2.

Store fixture suites with the organizer's verification files. The classroom
submission schema continues to accept only edits to the challenge scenario.
