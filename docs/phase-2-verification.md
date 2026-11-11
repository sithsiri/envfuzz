# Phase 2 verification

## Worker scaling

Eight frozen corridor scenarios ran on an eight-CPU ARM64 Docker Desktop VM.
Each worker had a 1.5 CPU limit and 1 GiB of memory. Every configuration saved
eight behavior results. These are end-to-end timings, including container
startup and teardown.

| Workers | Wall seconds | Behavior episodes per minute |
| ---: | ---: | ---: |
| 1 | 338.70 | 1.42 |
| 2 | 158.63 | 3.03 |
| 4 | 76.42 | 6.28 |
| 8 | 49.57 | 9.68 |

## Maps and timed edits

A timed chair move was logged at the requested 3.000 simulated seconds. All
360 LiDAR ranges differed between the adjacent recorded scans. The run also
saved reference and SLAM grids and generated the combined HTML report.
Synthetic checks covered map rotation, unknown cells, the T3 coverage gate,
and footprint reachability. The three-run empty-room baseline reached the
maximum map-error threshold of 1.0. This fixture's map errors therefore
provide diagnostic evidence, with no T3 discrimination at that threshold.

## Sampling pilot

Seed 42 generated two cases per layout and method across three layouts. All
three methods covered six categories. The coverage-guided method had one
infrastructure attempt that was retried successfully.

| Method | Behavior episodes | Goals reached | Distinct regression signatures |
| --- | ---: | ---: | ---: |
| Uniform | 6 | 4 | 2 |
| Stratified | 6 | 2 | 4 |
| Coverage-guided | 6 | 5 | 1 |

The pilot is a workflow check with six episodes per method. The comparison
and source bundles are under `runs/phase2-live/pilot/` in the verification
checkout. Three replays per corpus representative produced 17 valid runs
and one infrastructure failure. One of the 17 valid replays matched its
original signature. These cases remain candidates for diagnosis.

The minimizer also removed the single table from a controlled
short-deadline timeout fixture and reproduced the signature in two of three
valid reruns. [ADR 0004](adr/0004-replay-timing.md) records the timing and
stability measurements.

## Classroom fixtures

| Fixture | Successful calibration trials | Reference goals reached |
| --- | ---: | ---: |
| open-room | 3 | 2/3 |
| corridor | 3 | 3/3 |
| multi-room | 3 | 3/3 |

Each reference was evaluated in three isolated, resource-limited runs.
The corridor uses the archived 0.5× simulation profile after two
unthrottled calibration attempts hit Nav2 action acknowledgement
timeouts. The organizer scoreboard exported one verified reference
entry for each fixture.
