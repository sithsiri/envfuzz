# envfuzz contributor guide

The project is intentionally split between simulator-free Python contracts and
optional ROS 2 assets. Keep scenario validation, compilation, result contracts,
and oracle logic deterministic and testable without ROS or a display server.
Use simulation time for episode decisions; wall-clock time is only a watchdog.

