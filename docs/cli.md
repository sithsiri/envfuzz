# CLI reference

- `envfuzz scenario validate FILE` validates geometry and prints the scenario hash.
- `envfuzz scenario validate FILE --schema` prints the JSON Schema.
- `envfuzz compile FILE --output world.sdf` emits deterministic SDF.
- `envfuzz run FILE --output DIR` runs a live Gazebo and Nav2 mission. Use
  `--smoke` for a local bundle check, `--params-file` to supply a Nav2 YAML, or
  `--baseline FILE` to score the mission.
- `envfuzz replay BUNDLE --output DIR` repeats a live mission using its saved
  scenario, limits, and Nav2 parameters.
- `envfuzz report BUNDLE` writes a standalone trajectory SVG in the bundle.
- `envfuzz baseline [FILE] --n 10 --output DIR` measures mission duration and
  calibrates SLAM position error. Omit `FILE` for the default empty apartment.
- `envfuzz campaign MANIFEST --output DIR` plans and executes a navigation benchmark.
  Use `--plan-only` to generate or inspect the plan without Gazebo.
- `envfuzz fuzz --budget N --seed S --baseline FILE` runs and scores seeded
  scenarios. Use `--scenario FILE` to retain the same starting layout used in
  calibration or `--smoke` to exercise candidate generation without Gazebo.
- `envfuzz doctor` prints runtime information.

Live runs and replays require a new output directory. A non-successful mission
returns exit code 2. The result distinguishes navigation failure and
simulation-time timeout from launch failures and wall-clock watchdogs.
