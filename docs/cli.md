# CLI reference

- `envfuzz scenario validate FILE` validates geometry and prints the canonical hash.
- `envfuzz scenario validate FILE --schema` prints the JSON Schema.
- `envfuzz compile FILE --output world.sdf` emits deterministic SDF.
- `envfuzz run FILE --output DIR` runs one validated episode and writes a bundle.
- `envfuzz baseline --n 10 --output baseline.json` derives baseline thresholds.
- `envfuzz fuzz --budget N --seed S` runs valid candidates sequentially.
- `envfuzz doctor` prints runtime versions.

Exit statuses distinguish behavior outcomes (`completed`, `timeout`) from
infrastructure outcomes (`watchdog`, `launch_failed`, `simulator_failed`) in
`result.json`.
