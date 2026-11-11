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

For trusted workers on several hosts, put the campaign output on a shared
POSIX filesystem. Approve each worker ID once, run one worker per host, then
reconcile the authoritative results:

```sh
envfuzz campaign-approve examples/phase2-campaign.yaml --output /shared/pilot \
  --worker-id host-a --worker-id host-b --retries 2
envfuzz campaign-worker examples/phase2-campaign.yaml --output /shared/pilot --worker-id host-a
envfuzz campaign-reconcile /shared/pilot
```

The shared claim ledger gives each numbered attempt a bounded lease. When a
lease expires, the case is retried within the budget. The ledger keeps every
attempt and commits one authoritative result per case. Single-host `campaign`
remains the default. [Multi-host campaigns](multi-host.md) covers leases,
diagnostics, filesystem limits, and the single-host fallback.

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

## Keepout Filter pilot

The default Nav2 parameters do not load a Keepout Filter. The small pilot below
compares the same straight start-to-goal mission with the filter disabled and
enabled on both costmaps, with an inflation filter to account for the robot's
footprint. It uses AMCL so the generated filter mask shares the static
navigation map's frame and origin. Each run records the restricted-zone event
and goal outcome; the filtered run should reach the goal without a zone event.

Build the updated image, then run the paired episodes. Use `tmux` if you want
an interactive session that survives terminal disconnects.

```sh
tmux new -s envfuzz-keepout
docker build -t envfuzz:jazzy .
mkdir -p runs/keepout-pilot
docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
  run examples/keepout-pilot.yaml --profile examples/amcl-localization-profile.yaml \
  --output runs/keepout-pilot/no-filter --timeout 45 --watchdog 180
docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
  run examples/keepout-pilot.yaml --profile examples/amcl-localization-profile.yaml \
  --keepout-filter --output runs/keepout-pilot/keepout-filter-inflated \
  --timeout 45 --watchdog 180
```

Compare `result.json` and `telemetry.jsonl` in the two bundles. The control
should enter `center-keepout`; the enabled run should go around it and complete.
A filtered run that fails to start the filter servers or costmap plugins is an
infrastructure/configuration failure, not evidence about avoidance.

For five fresh matched pairs, the repeat driver alternates which condition runs
first. Launch it in a detached `screen` session so it survives a terminal
disconnect; reconnect with `screen -r envfuzz-keepout-repeats`:

```sh
mkdir -p runs/keepout-pilot-repeats-20261111
screen -dmS envfuzz-keepout-repeats bash -c \
  'bash scripts/keepout_pilot_repeats.sh > runs/keepout-pilot-repeats-20261111/runner.log 2>&1'
screen -ls
tail -f runs/keepout-pilot-repeats-20261111/runner.log
```

This protects against terminal disconnects, not host shutdown. The repeat
bundles are written under `runs/keepout-pilot-repeats-20261111/`.

## Dynamic obstacle pilots

The dynamic-obstacle driver compares a stationary off-route object with (a) a
scripted actor and (b) a timed object move, each of which crosses the robot's
route. It runs three repeats per condition, records bumper contacts, and
alternates run order. Start it in `screen`:

```sh
mkdir -p runs/dynamic-obstacle-pilot-20261111
screen -dmS envfuzz-dynamic-obstacles bash -c \
  'bash scripts/dynamic_obstacle_pilot.sh > runs/dynamic-obstacle-pilot-20261111/runner.log 2>&1'
screen -ls
```

Compare each `control-N` bundle with its `actor-N` or `timed-N` partner. Check
`result.json`, `metrics.json`, `contacts.jsonl`, `events-applied.json`, and
`scans.jsonl`: the moving obstacle should appear on the scan, trigger a detour,
and leave the robot able to complete the mission without contact.

## LiDAR robustness pilot

This pilot repeats a fixed obstacle course with the default LiDAR and with a
120° field of view plus 2 cm range noise. It alternates run order and records
three runs per condition. Compare goal outcomes, path length, map metrics, and
position RMSE in the bundles:

```sh
mkdir -p runs/lidar-degradation-pilot-20261111
screen -dmS envfuzz-lidar-pilot bash -c \
  'bash scripts/lidar_degradation_pilot.sh > runs/lidar-degradation-pilot-20261111/runner.log 2>&1'
screen -ls
```

Pilot results (three runs per condition): the nominal and degraded sensors
each completed all three goals. Median path lengths were 4.83 m and 4.89 m,
and median position RMSE was 0.013 m for both. This mild degradation did not
show a meaningful navigation regression in this obstacle course.

The dynamic-obstacle pilot also completed all runs. The stationary controls had
a 3.77 m median path; scripted actors increased it to 4.68 m and timed moves to
4.76 m. All actor and timed-move updates were applied, all goals were reached,
and no bumper contacts were recorded. This is evidence that both timeline
interfaces perturb navigation as intended in this fixture.

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
