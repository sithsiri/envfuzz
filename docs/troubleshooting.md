# Troubleshooting

If `scenario validate` fails, correct the field or named geometric violation
before attempting a run. A `watchdog` or `simulator_failed` result is an
infrastructure outcome and preserves `logs.txt` for diagnosis. Docker image
builds require a ROS 2 Jazzy apt source and a Docker-capable host; no display,
NVIDIA runtime, or X11 server should be configured.
