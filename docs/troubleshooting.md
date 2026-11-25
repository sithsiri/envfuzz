# Troubleshooting

If `scenario validate` fails, correct the field or named geometric violation
before attempting a run. A `watchdog` or `simulator_failed` result is an
infrastructure outcome and preserves `logs.txt` for diagnosis. Docker image
builds require a ROS 2 Jazzy apt source and a Docker-capable host; no display,
NVIDIA runtime, or X11 server should be configured.

If a mission ends with `navigation_goal_outside_map`, the requested goal is
outside the active navigation grid. The runner does not send that goal to
Nav2 or move it to a different coordinate. Inspect `map-bounds-failure.json`
for the requested scenario-world goal and observed grid extent. This is a map
coverage failure; `Nav2 reported action status ...` means Nav2 received an
in-bounds goal but did not complete it, which can indicate a planner or
execution failure. `map_bounds_unavailable` indicates that no usable `/map`
grid arrived before the simulation-time mission deadline.
