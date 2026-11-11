# Phase 3 navigation backend comparison

Status: **partial evidence for task 3.2 (P3-05).** This record compares the
default `slam_toolbox` backend with Nav2 AMCL map-based localization on one
CPU host. It does **not** include an alternative SLAM backend. The
three-way comparison required by the simulation-profiles spec is therefore
incomplete. See [Alternative SLAM backend](#alternative-slam-backend).

## Host and image

| Item | Value |
| --- | --- |
| Host | Apple Silicon Mac, Docker Desktop 29.6.2, `aarch64` VM, 10 CPUs, 7.75 GiB RAM, kernel 6.12.76-linuxkit, no NVIDIA GPU |
| Image | `envfuzz:phase3-base`, ID `sha256:4ac82b729347d39c24524fa48c303ca2d0c70dd13543dc59d96b76c58468edff`, built from commit `69364f7` with `git archive HEAD` |
| Pinned packages used | `ros-jazzy-slam-toolbox` 2.8.5, `ros-jazzy-navigation2` 1.3.13 (AMCL and map server), `ros-jazzy-rtabmap-ros` 0.23.13 |
| Worker limits | `--network none`, 2 CPUs, 1536 MiB memory with no swap, one episode per container |
| Mission limits | 25 simulated seconds per mission and an 80 s wall-clock watchdog, as in the [Phase 3 replay pilot](phase-3-verification.md) |

## Frozen suite

The suite has six scenarios. The four examples were taken from `HEAD`, and the
driver refuses to run if the working tree differs from it. The two seeded cases
come from `envfuzz campaign examples/phase2-campaign.yaml --plan-only` with seed 42.

| Scenario | Source | SHA-256 |
| --- | --- | --- |
| `scenario` | `examples/scenario.yaml` | `76dd9478d1b5ee3424936bc7013949911d09843ab33c9c720fd0b846ca2e23df` |
| `timed-move` | `examples/timed-move.yaml` | `8b4053e6a4d75a879badaf3b2758462f12840192e2d8d90e0a6f4c4328f7e263` |
| `scripted-actor` | `examples/scripted-actor.yaml` | `f90949a0b15c02c6fd58d042d01772daf58f5bcd22800d54fbbdb8e7a099cd02` |
| `friction-patch` | `examples/friction-patch.yaml` | `d62056c31e573363a6c789d010ca15c1e3b9bcaaa8f1f2ea34a46952d7b18cc4` |
| `seed42-apartment-case0` | plan case 0 (apartment, uniform) | `4905425abe2c165cfe735e4ee4bc57493a024f6787b2fd7e9994204701e07d93` |
| `seed42-corridor-case6` | plan case 6 (corridor, uniform) | `efd3b5998ec77096f5c41bf461b2a7d8efbef76e08092e8f262fe57b4240b406` |

The seed plan file has SHA-256
`2bb79e4b93a04e9973adde3f7befc72c7e613ecfbbbb4e864caf2de86502fcfa`. The
default backend ran with no profile file. AMCL used
`examples/amcl-localization-profile.yaml` with SHA-256
`80de51de06c6c16550e5c4dd734eb3809a3294b222dd17ce43f2ca1d9480e6f5`.

## Method

`docker/backend_comparison.py` ran one worker thread per backend at the same
time. Both backends therefore shared the host under similar contention, with at
most two simulation containers running at once. For each scenario and backend,
the driver:

1. Calibrated a matching baseline with `envfuzz baseline --n 3`. If a
   calibration attempt failed, it made one more attempt.
2. Ran three scored episodes with `envfuzz run --baseline`. After an
   infrastructure result it allowed up to two retries.

Every container wrote its cgroup v2 `memory.peak` and `cpu.stat` when it
finished. A sampler stored raw `docker stats` rows in `docker-stats.jsonl`
(668 rows). The run started at Unix time 1794375271.8 and finished at
1794376308.6, about 17.3 minutes of wall time.

## Results

Scored episodes: each backend has 6 scenarios with 3 episodes each, for 18
episodes per backend. Ranges show the median and the minimum to maximum.

| Measure | `slam_toolbox` (default) | `amcl` (map-based localization) |
| --- | ---: | ---: |
| Goals reached | 18/18 | 18/18 |
| Infrastructure attempts in scored runs | 0 | 0 |
| Episodes with a non-zero score | 0 | 5 (total score 15) |
| Oracle events | none | 5 × T3 `map_error`, 1 × T2 `pose_error` |
| Nav2 recoveries | 1 | 0 |
| Simulated mission duration (s) | 5.232 (4.357–10.246) | 5.371 (4.410–8.909) |
| Episode wall time (s) | 21.454 (18.354–32.119) | 21.320 (17.530–40.585) |
| End-to-end real-time factor | 0.243× (0.156–0.326) | 0.244× (0.182–0.290) |
| Mission-only real-time factor | 0.479× (0.346–0.636) | 0.483× (0.397–0.628) |
| Position RMSE against ground truth (m) | 0.005 (0.001–1.000) | 0.038 (0.017–1.036) |

Per scenario (scored, 3 episodes each):

| Scenario | Backend | Goals | Score | Events | Median sim duration (s) | Median RMSE (m) |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| scenario | slam_toolbox | 3/3 | 0 | – | 4.465 | 0.003 |
| scenario | amcl | 3/3 | 0 | – | 4.412 | 0.030 |
| timed-move | slam_toolbox | 3/3 | 0 | – | 4.395 | 0.005 |
| timed-move | amcl | 3/3 | 0 | – | 4.484 | 0.019 |
| scripted-actor | slam_toolbox | 3/3 | 0 | – | 8.932 | 0.003 |
| scripted-actor | amcl | 3/3 | 9 | 3 × T3 map_error, 1 × T2 pose_error | 8.894 | 0.045 |
| friction-patch | slam_toolbox | 3/3 | 0 | – | 4.675 | 0.989 |
| friction-patch | amcl | 3/3 | 0 | – | 5.079 | 1.034 |
| seed42-apartment-case0 | slam_toolbox | 3/3 | 0 | – | 6.072 | 0.005 |
| seed42-apartment-case0 | amcl | 3/3 | 0 | – | 5.923 | 0.042 |
| seed42-corridor-case6 | slam_toolbox | 3/3 | 0 | – | 5.710 | 0.004 |
| seed42-corridor-case6 | amcl | 3/3 | 6 | 2 × T3 map_error | 5.757 | 0.033 |

On `friction-patch`, both backends reached the goal but recorded about 1 m
position RMSE. This divergence was not investigated further. All calibrated
position-error thresholds were at the 0.1 m floor.

### AMCL map-error events are an artefact

Under AMCL, `slam-map.json` is the static `map_server` map. The scored
`map_error` therefore measures how well the exported map aligns with the
world through AMCL's initial pose estimate, plus any objects that move after
export. It does not measure mapping quality.

The `scenario` calibration shows this. The same static map gave
`map_error` 0.0, 0.317 and 0.360 across three trials. The `scripted-actor`
baseline was 0.060–0.066, while its scored runs were 0.197–0.294. The
`corridor` baseline was 0.0–0.065, while its scored runs were 0.067–0.212.
Every scored AMCL T3 event comes from this effect.

For `slam_toolbox`, map coverage stayed between 0.149 and 0.670. This is
sometimes below the 0.2 `min_map_coverage` gate, which suppresses those map
events. The AMCL T3 count should not be read as a localization regression.
Deciding whether `map_error` applies to AMCL is a design question.

## Resource measurements

These figures cover scored containers: 18 per backend, one episode each. CPU
cores are cgroup `usage_usec` divided by container wall time. Peak CPU is the
highest raw `docker stats` sample per container.

| Measure | `slam_toolbox` | `amcl` |
| --- | ---: | ---: |
| Peak container memory (MiB) | 962.8 (945.0–980.5) | 939.8 (935.5–958.0) |
| Mean CPU cores used | 1.761 (1.292–1.847) | 1.760 (1.297–1.862) |
| Peak `docker stats` CPU % | 213.2 (206.0–234.0) | 214.4 (197.1–230.5) |
| Container wall time (s) | 22.3 (19.2–33.1) | 22.3 (18.3–41.8) |

Both backends ran at the 2-CPU quota for most of each episode. For example,
the AMCL `scenario` calibration container recorded 477 of 581 CFS periods
throttled (`resources/bc-amcl-scenario-cal1.txt`). Neither backend came close to the 1536 MiB limit.

## Infrastructure failures and calibration aborts

| Backend | Event | Evidence |
| --- | --- | --- |
| slam_toolbox | Watchdog in `scenario` calibration trial 2. The retry completed. | `slam_toolbox/scenario/calibration-1/trial-0002` |
| slam_toolbox | Watchdog in `seed42-corridor-case6` calibration trial 1. The retry completed. | `slam_toolbox/seed42-corridor-case6/calibration-1/trial-0001` |
| amcl | `scripted-actor` calibration attempt 1 aborted at trial 1. Nav2 returned status 6 (aborted). The second attempt completed. | `amcl/scripted-actor/calibration-1/trial-0001` |
| amcl | The pre-run verification baseline aborted at trial 3 with the same status 6 cause. | `verify-amcl/baseline/trial-0003` |

Both watchdogs stalled during Nav2 lifecycle start-up. The logs show
`failed to send response to /controller_server/change_state (timeout)`, and no
telemetry was produced. Both AMCL aborts logged `Timed out while waiting for
action server to acknowledge goal request for compute_path_to_pose`. That is
the load-related acknowledgement timeout already described in
[ADR 0004](adr/0004-replay-timing.md).

The aborts are classified as behavior, but `envfuzz baseline` stops at the first
trial that does not complete, so each one cost a calibration attempt. The scored
phase had no infrastructure attempts for either backend.

Totals: across scored and calibration runs, the default backend made 38 episode
attempts with 2 infrastructure attempts. AMCL made 37 attempts with 0
infrastructure attempts and 1 aborted calibration trial. The AMCL figure
excludes the separate verification run.

## AMCL verification in the pinned runtime

- One default-profile smoke episode completed in 17.9 s wall time with a
  0.249× end-to-end real-time factor (`smoke-default`).
- Each AMCL bundle stores `localization-map.pgm` (P5, 204 × 204 for the 10 m
  example world at 0.05 m) and `localization-map.yaml`. `run.json` resolves
  `navigation_backend: amcl`, and `profile.yaml` archives the profile.
  `map_server` starts, and `slam_toolbox` does not appear in the AMCL logs.
- Each AMCL baseline includes `simulation_profile_sha256` and
  `localization_map_sha256` in its identity. Scoring with the matching AMCL
  baseline succeeded in all 18 scored episodes.
- Mismatch rejection was checked offline only, not with a live simulator run.
  `apply_baseline` rejected the AMCL `scenario` baseline for a `slam_toolbox`
  bundle with `Baseline world, mission, or Nav2 parameters do not match this
  run`, and accepted it for an AMCL bundle.
- Code-reading observation, not exercised live: `run_gazebo` checks baseline
  identity only after the mission. A mismatched baseline is therefore
  reported as `simulator_failed` (infrastructure) after a full episode, not
  refused before launch.

## Alternative SLAM backend

No alternative SLAM backend ran in this comparison.

- **GPU gate behaves as specified on this CPU host.** `envfuzz run
  --profile examples/rtabmap-camera-profile.yaml` exited with code 2 within
  about 1 s. It reported `The RTAB-Map camera profile requires an NVIDIA GPU
  exposed to the runtime` and created no bundle directory. `nvidia-smi` is
  absent from the image, and `docker run --gpus all` fails on this host with
  `no known GPU vendor found`. The profile validator rejects
  `navigation_backend: rtabmap` without the camera and the camera without
  `rtabmap`. The default profile was unaffected, with 18/18 goals in the
  default leg above.
- **Installed packages.** The image contains RTAB-Map 0.23.13 (`rtabmap_slam`,
  `rtabmap_odom` with `icp_odometry`, `rtabmap_launch`, `rtabmap_demos`) and
  `libpointmatcher` 1.4.2. Cartographer is not installed.
- **Lidar-only RTAB-Map looks feasible but is unverified.** The installed
  `rtabmap_demos/launch/turtlebot3/turtlebot3_scan.launch.py` configures
  RTAB-Map for TurtleBot3 with `/scan`, wheel odometry and no camera. It uses
  `subscribe_scan`, ICP registration (`Reg/Strategy=1`) and
  `Reg/Force3DoF=true`. A throwaway launch override that replaces SLAM
  Toolbox with that node was prepared at
  `rtabmap-lidar-probe/headless.launch.py`. It was **not run**, because the
  Docker host was needed for another stage. No live result exists.
- **What wiring it would take.** It would need a CPU lidar-only RTAB-Map
  backend value that the validator does not tie to `camera_profile`, a launch
  branch that starts `rtabmap_slam` with the scan-only parameters, and Nav2
  started without SLAM Toolbox or AMCL (`use_localization: False`). It would
  also need a check that RTAB-Map's `/map` grid and `map` → `odom`
  transform feed the existing telemetry and map metrics.
- **Design and spec text that would change.** Decision 1 in `design.md` ties
  RTAB-Map to the RGB-D camera and the GPU check. The spec's "GPU camera
  profile" scenario and `docs/phase-3-profiles.md` describe `rtabmap` only as
  the camera backend. A CPU lidar-only variant would need its own backend
  identifier or a decoupled sensor field, and a spec statement that it is not
  GPU-gated. The other path is to run the existing camera profile on an
  NVIDIA host.

## Limitations

- The sample is small: 6 scenarios × 3 scored episodes per backend, with
  3-trial baselines on one ARM64 Docker Desktop host. Every goal was
  reached, so the suite does not separate the backends on outcome.
- Timing varies with load, as recorded in [ADR 0004](adr/0004-replay-timing.md).
  The two backends ran at the same time to share contention, but
  their scenario order drifted apart during the run.
- The end-to-end real-time factor includes start-up and teardown. The
  mission-only factor comes from `metrics.json`.
- AMCL starts from the ground-truth initial pose, so it is not a
  global-localization test.
- Open design question for the oracle owner: under AMCL, the map compared by
  `map_error` is the static exported map. The metric therefore measures map
  alignment through AMCL's initial estimate, not mapping quality, so its T3
  events are not comparable with those from the SLAM backends. See
  [AMCL map-error events are an artefact](#amcl-map-error-events-are-an-artefact).
  This record makes no code change for it.

## Evidence

Bundles and raw data are gitignored under `runs/phase3-resume/backend-comparison/`:

- `frozen/` contains `plan.json` (suite and hashes), `host.json` (`docker
  info`, `docker version` and the image inspect), `image.txt`, `results.csv`
  (one row per episode attempt), `containers.csv` (cgroup peak memory and CPU
  per container), `docker-stats.jsonl` (raw samples), `summary.json`,
  `console/`, `resources/` and the per-backend bundles.
- `smoke-default/`, `verify-amcl/`, `gpu-gate/`, `seed-plan/` and
  `rtabmap-lidar-probe/` hold the other checks.

To recompute the summary, run
`python docker/backend_comparison.py --summarize runs/phase3-resume/backend-comparison/frozen`.
