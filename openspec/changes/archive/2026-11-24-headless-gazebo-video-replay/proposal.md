# Proposal

## Why

Video recording currently captures the host's X11 display while Gazebo runs in server-only headless mode. On the project's headless server there is no `DISPLAY`, and even on a machine with one the recorded screen is not guaranteed to show Gazebo, so reviewers cannot reliably see how a robot moved during a failed case. An isolated, opt-in graphical replay would provide an actual Gazebo view without requiring a physical monitor or disturbing other display sessions.

## What Changes

- Add a replay-video path that runs the selected case in Gazebo's graphical simulator and records that view on a headless server.
- Isolate the graphical display and capture process from host display sessions, with containerized execution as the target deployment model.
- Preserve the current headless replay and campaign behavior when video is not requested, and retain explicit video status and replay-origin metadata.
- Persist review replay job history with the campaign so completed replay evidence remains visible after the review server restarts, scoped to its originating case and attempt.
- Show each case's simulation duration in the review queue.
- Establish and document the rendering runtime requirements, including whether software rendering is sufficient or GPU access is required.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `episode-video-evidence`: Define opt-in video replay using an isolated Gazebo GUI display on a headless host, with traceable video output and safe failure behavior.

## Impact

Likely affected areas include `src/envfuzz/episode_video.py`, the replay command and review interface in `src/envfuzz/cli.py` and `src/envfuzz/review_gui.py`, Gazebo launch configuration under `ros2_ws/src/envfuzz_sim`, and Docker runtime assets under `docker/`. The change adds graphical rendering and video-capture dependencies to the optional replay path; ordinary simulation remains headless and does not require a display.
