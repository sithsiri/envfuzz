# Quickstart

Install the package with `python -m pip install -e '.[test]'`, then validate a
scenario with `envfuzz scenario validate scenario.yaml`. Run the simulator-free
deterministic smoke fuzzer with `envfuzz fuzz --budget 20 --seed 42`.

The optional ROS 2 image is CPU-only and headless:

```sh
docker build -t envfuzz:jazzy .
docker run --rm envfuzz:jazzy envfuzz doctor
```

`runs/results.jsonl` is append-only. A result bundle always preserves the
scenario, compiled world, structured result, runtime versions, timings, and logs.

For a local contract smoke run:

```sh
envfuzz scenario validate examples/scenario.yaml
envfuzz run examples/scenario.yaml --output runs/example
```

Baseline thresholds use `max(floor, mean + 3*population_stddev)` and are saved
with the world and mission identity. The current development host has no Docker
daemon, so image size/build-time measurements and real Gazebo LiDAR throughput
are recorded as pending in ADR 0003 rather than fabricated.
