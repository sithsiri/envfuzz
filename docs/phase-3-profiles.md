# CPU robot and navigation profiles

Profiles are version 1 YAML files chosen by the organizer or local operator.
The currently supported navigation backend is `slam_toolbox`. The robot
allowlist includes the default `turtlebot3_waffle` and `compact_vacuum`.

| Profile | Collision chassis | LiDAR | Bumper |
| --- | --- | --- | --- |
| Default | 0.22 m radius, 0.089 m tall | Full circle at the existing default rate | Optional |
| Compact example | 0.22 m radius, 0.06 m tall | 270° at 8 Hz, 360 rays, 0.005 m Gaussian range noise | Enabled |

The compact model keeps the stock wheelbase, footprint radius, and LiDAR
mounting frame. Its chassis geometry is lower, so it remains compatible with
the current CPU reachability checks and Nav2 parameters. The compact profile
illustrates the allowed LiDAR field of view, scan rate, sample count and range
noise settings. The [sensor guide](phase-3-sensors.md) describes the optional
observation flags.

```sh
envfuzz baseline examples/scenario.yaml --n 1 \
  --profile examples/compact-vacuum-profile.yaml --output runs/compact-baseline
envfuzz run examples/scenario.yaml --profile examples/compact-vacuum-profile.yaml \
  --baseline runs/compact-baseline/baseline.json --output runs/compact
envfuzz replay runs/compact --output runs/compact-replay
```

Use the organizer's intended repeat count for published calibration. The
one-trial example is a smoke check. A scoring baseline must match the
mission, Nav2 settings and simulation profile. Existing bundles and
baselines retain their implicit default settings. New bundles store the
resolved settings in `run.json` and archive the supplied profile as
`profile.yaml` for replay.

In a classroom challenge, set `robot: compact_vacuum` and point `profile` at
a matching file inside the challenge directory. The optional
`navigation_backend` defaults to `slam_toolbox` and must agree with the
profile. Use separate baselines when changing robot or sensor settings.

## Headless smoke results

Both profiles completed one calibration trial and a separate scored episode
with the resulting baseline. The default profile scored zero. The compact
profile completed its goal and recorded a T3 map event in the scored run.
A one-trial calibration checks wiring and compatibility. Use a repeated
calibration before comparing mapping behavior.
