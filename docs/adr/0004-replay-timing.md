# ADR 0004. Record replay agreement alongside speed

## Decision

Keep the default simulation timing profile and make faster or throttled
profiles explicit. Record the profile in each run bundle. Use a matching
baseline when scoring replays under a different profile.

## Observations

The Gazebo Harmonic control service advanced a paused world from iteration
0 to iteration 20 and left it paused. The command and stats round trip took
0.395 wall seconds in Docker Desktop. This prototype verified physics stepping.
A complete lock-step mode would also need an acknowledgement from each
Nav2 and SLAM consumer that it had processed the observations for that step.

The reference mission completed at a mission real-time factor of 0.66×.
Two of three replays under a one-CPU cap matched the reference outcome.
A throttled replay completed at 0.48×. The profile requesting 5× with a 2 ms
physics step, 4 Hz LiDAR and 180 rays reached up to 2.02× during its missions,
and all three missions failed. The additional Nav2 and SLAM tuning trial
reached 1.78× and also failed. Logs captured navigation action acknowledgement
timeouts under load. These measurements fell short of the initial 95%
agreement and 5× performance targets.

## Reproduce

Run the paused-world probe inside the image with
`docker/lockstep_probe.py`. Use `envfuzz replay` with different CPU limits
and archived Nav2 settings to measure agreement. The run bundle reports both
the mission real-time factor and the full episode factor, which includes
startup and teardown.

[Gazebo simulation pause and step control](https://gazebosim.org/api/sim/9/pause_run_simulation.html)
and [Nav2 action acknowledgement settings](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/configuring_bt_navigator/)
describe the interfaces exercised in these checks.

## Phase 3 follow-up

The frozen pilot and replay of the original regression representatives are
documented in the [Phase 3 verification record](../phase-3-verification.md).
