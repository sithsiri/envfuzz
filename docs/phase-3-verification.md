# Phase 3 verification

## Frozen replay pilot

The rerun used the original 18-case Phase 2 plan. It contains two scenarios per
layout and method, all generated with seed 42. The original and repeated plan
files share SHA-256
`8e6c237c9d719ee5fd434581b26d1f7c379979221b734bb3754f0d0928d8f43a`.
The host CLI started from commit `e7a0c3a`. Both pilot and replay used the
`envfuzz:phase2-verified` image by immutable local ID
`sha256:b6c2d1c807737672a2a004cbee3f09a2d11cc92c2fb0e17fa54e74e15b968344`.

The host was Docker Desktop 29.9.2 on ARM64 with eight VM CPUs, about 7.75 GiB
of RAM, and LinuxKit kernel 7.0.14. Two workers ran concurrently with two CPUs
and 1536 MiB each. The plan retained the 25 simulated second mission limit and
80 wall-second watchdog. The repeat kept its own output directory so the
original Phase 2 evidence was preserved.

| Sampler | Phase 2 goals reached | Phase 3 rerun goals reached | Phase 3 infrastructure attempts |
| --- | ---: | ---: | ---: |
| Uniform | 4/6 | 6/6 | 0 |
| Stratified | 2/6 | 6/6 | 0 |
| Coverage | 5/6 | 6/6 | 1 |

The infrastructure attempt was a watchdog and its retry completed. Across
the 18 valid episodes, the end-to-end real-time factor ranged from 0.214× to
0.477×, with a median of 0.420×. Since every valid episode reached its goal,
the repeated pilot produced no navigation regression representatives.

The original six Phase 2 representatives were frozen separately, including
their archived scenarios, runtime settings, and expected signatures. The
representative list has SHA-256
`570e691eed7fcc5876907840aa6766545bf8b0551e05adc8ba46f0a7211567b3`.

| Original case | Valid replays | Matching signatures | Infrastructure attempts | Median end-to-end real-time factor |
| --- | ---: | ---: | ---: | ---: |
| 13 | 3 | 0 | 0 | 0.336× |
| 14 | 2 | 0 | 1 | 0.398× |
| 3 | 3 | 0 | 0 | 0.437× |
| 4 | 3 | 0 | 0 | 0.284× |
| 9 | 3 | 0 | 0 | 0.368× |
| 10 | 3 | 0 | 0 | 0.357× |

None of the 17 valid replays matched the expected signature. Sixteen reached
their goal. One ended with a different failure signature, and an additional
attempt hit the infrastructure watchdog. End-to-end real-time factors ranged
from 0.228× to 0.448×, with a median of 0.351×. The original Phase 2 pilot
matched 1 of 17 valid replays. Both pilots leave these fixtures diagnostic.

## Fixture admission

Use the Phase 2 target of at least 95% agreement for required replay checks.
Require 20 valid replays per fixture under its recorded profile before
considering admission. Report infrastructure attempts separately, including
their causes. The three-run diagnostic pilot per representative gives an
early stability signal. The required CI corpus gained no fixtures in this run.

The end-to-end real-time factors above include startup and teardown. The
mission-only speed target and earlier timing probes are documented in
[ADR 0004](adr/0004-replay-timing.md). Those measurements use a different
denominator and remain separate from the figures in this report.

## Classroom isolation

The multi-room reference submission reached its goal in all three valid runs.
The median mission duration was 5.316 simulated seconds. Inspection of the
first active worker confirmed a private network namespace with networking
disabled, a read-only root, an unprivileged container, and private IPC and
PID namespaces. The only bind mounts were read-only inputs and writable
results. The worker had a two-CPU quota and a 1536 MiB memory limit.

Simulator-free checks cover malformed and deeply nested JSON, unsupported
submission fields, oversized or non-regular files, and challenge assets that
resolve outside their manifest directory.

## Passive LiDAR surface probe

A separate headless Gazebo 8.15.0 probe recorded five rays per surface with an
opaque panel behind each test panel. The native renderer reported the
configured return intensity while continuing to measure range to the front
surface even at 95% visual transparency.

| Fixture | Center-ray range | Center-ray intensity |
| --- | ---: | ---: |
| Opaque | 1.970 m | 120.00 |
| Visually transparent | 1.970 m | 19.99 |
| Reflective | 1.970 m | 500.01 |
| Dark | 1.970 m | 5.00 |
| Thin | 2.970 m | 250.00 |

The thin panel was missed by the sampled rays. The center ray hit its
backing panel. The glass and dark fixtures still produced front-surface
returns. Task 2.1 remains open pending a sensor model that represents
transmission and reflectivity-dependent dropouts in simulation. The
[Gazebo Harmonic renderer source](https://github.com/gazebosim/gz-rendering/blob/gz-rendering8/ogre2/src/Ogre2GpuRays.cc)
shows how the native pass encodes range and return intensity.

## Local evidence

The full bundles are gitignored. The verification checkout stores them under
`runs/phase3-replay.1kMgUq/`:

- `plan.json`, `host.json`, `image.txt`, `results.csv`, and `analysis.json`
  record the repeated campaign.
- `original-corpus/frozen-corpus.json` and `original-corpus/analysis.json`
  record the original representative list and the repeated signatures.
- `isolation-inspect.json` and `isolation-check/evaluation.json` record the
  live organizer worker inspection and reference evaluation.
- `material-probe.json` records the raw ray ranges and intensities. The
  matching SDF and runner are in `material-probe/`.

## Additional sampling decision

Phase 3 retains uniform, stratified and coverage-guided sampling. At six
completed episodes per strategy on the same frozen plan, all three methods
covered six categories. The rerun reached all goals for every method,
while the original run produced seven navigation failures. The original
failure signatures reproduced in zero of 17 valid Phase 3 replays.
There is no evidence from this pilot to justify adopting an additional
strategy. A future comparison needs fixtures whose reference outcomes are
stable and should report coverage, compute cost and replay agreement under
the same per-strategy budget.
