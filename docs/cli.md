# CLI reference

- `envfuzz scenario validate FILE` validates geometry and prints the scenario hash.
- `envfuzz scenario validate FILE --schema` prints the JSON Schema.
- `envfuzz compile FILE --output world.sdf` emits deterministic SDF.
- `envfuzz run FILE --output DIR` runs a live Gazebo and Nav2 mission. Use
  `--smoke` for a local bundle check, `--params-file` to supply a Nav2 YAML, or
  `--baseline FILE` to score the mission.
- `envfuzz replay BUNDLE --output DIR` repeats a live mission using its saved
  scenario, limits, and Nav2 parameters. Add `--video` to record Gazebo's GUI
  view in an isolated virtual display; this requires the optional
  `envfuzz:jazzy-video` image and can be overridden with
  `--video-docker-image IMAGE`.
- `envfuzz review-export CAMPAIGN` exports timeout and mission-failure candidates
  as `review-candidates.json` and `review-candidates.csv`.
- `envfuzz review-update CAMPAIGN` creates or updates a structured manual
  root-cause record.
- `envfuzz review CAMPAIGN --serve` starts a loopback-first web GUI for filtering
  failure candidates, inspecting bundles, saving classifications, and starting
  replay jobs. Use `--no-open`, `--host`, `--port`, `--replay-backend docker`,
  `--docker-image IMAGE`, or `--video-docker-image IMAGE` to control the local
  server and replay backends.
- `envfuzz report CAMPAIGN --outcome timeout --review-status confirmed`
  filters campaign review evidence.
- `envfuzz report BUNDLE` writes a standalone trajectory SVG in the bundle.
- `envfuzz baseline [FILE] --n 10 --output DIR` measures mission duration and
  calibrates SLAM position error. Omit `FILE` for the default empty apartment.
- `envfuzz campaign MANIFEST --output DIR` plans and executes a navigation benchmark.
  Use `--plan-only` to generate or inspect the plan without Gazebo.
- `envfuzz campaign-approve MANIFEST --output DIR --worker-id ID` freezes the
  plan, creates the shared claim ledger, records `--retries`, and approves
  worker IDs. Repeat `--worker-id` once per worker.
- `envfuzz campaign-worker MANIFEST --output DIR --worker-id ID` claims and runs
  jobs from a shared campaign under `--lease-s`, which must exceed the
  watchdog plus 120 s. Use `--no-wait` to exit instead of waiting for other
  workers' live leases. The worker exits with code 2 after
  `--max-consecutive-failures` (default 3) infrastructure failures in a row,
  or when it recorded only infrastructure failures.
- `envfuzz campaign-reconcile DIR` writes the authoritative `results.csv` and
  the `claims.json` attempt audit. It returns exit code 2 until every case has
  an authoritative result. See [Multi-host campaigns](multi-host.md).
- `envfuzz fuzz --budget N --seed S --baseline FILE` runs and scores seeded
  scenarios. Use `--scenario FILE` to retain the same starting layout used in
  calibration or `--smoke` to exercise candidate generation without Gazebo.
- `envfuzz doctor` prints runtime information.

Live runs and replays require a new output directory. A non-successful mission
returns exit code 2. The result distinguishes navigation failure and
simulation-time timeout from launch failures and wall-clock watchdogs.
