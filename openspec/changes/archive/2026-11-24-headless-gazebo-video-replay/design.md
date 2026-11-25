# Design

## Context

See proposal.md for motivation. The current episode runner launches `gz sim` in server-only headless mode. `episode_video.py` independently uses `ffmpeg`'s X11 input to record the process environment's `DISPLAY`; this can neither start Gazebo's GUI nor guarantee it records the simulator. Review replay jobs already have a backend boundary and bundle-relative video metadata.

The ordinary simulator is designed to run without ROS-independent Python paths requiring a display. The graphical path must remain opt-in and isolated, and must not use the host's display socket or change an original campaign bundle.

## Goals / Non-Goals

**Goals:**

- Produce a video of Gazebo's actual graphical simulator view for a selected replay on the headless server.
- Keep the GUI, virtual display, and capture process isolated from host display workloads.
- Keep normal headless runs and replays free of GUI dependencies and preserve the original case as the reference evidence.
- Reuse the current replay origin and video status metadata so reviewers can trace output and understand failures.

**Non-Goals:**

- Add simulated robot-camera video or reconstruct a scene from telemetry.
- Record every campaign episode or make video a prerequisite for simulation.
- Change scenario semantics or treat a video replay's navigation score as a replacement for the original episode result.

## Decisions

1. **Run video as a dedicated graphical replay.** The video request will select a replay mode that launches Gazebo's GUI for the stored scenario and runtime configuration. Existing replay without video continues through the server-only path. This cleanly separates visual evidence from campaign execution and avoids rendering overhead on ordinary runs. The video and replay result remain in a distinct replay bundle linked to the original bundle.

2. **Use an isolated container and virtual X display for the graphical runtime.** The video backend will own its X server and capture process inside a container, write artifacts to the selected replay output, and stop its processes when the bounded replay job finishes or is cancelled. It will not mount or read the host X11 socket or depend on host `DISPLAY`. This uses the existing Docker replay boundary and avoids interfering with unrelated server display tasks. A host-only Xvfb invocation was considered, but would leave rendering dependencies and process isolation coupled to the server installation.

3. **Start with Mesa software rendering; make GPU access optional only if needed.** First establish whether the project's Gazebo GUI and rendering engine work reliably with software OpenGL in the isolated container. If viable, use that as the default because it fits headless CPU servers. If not, document and implement an explicit GPU-enabled runtime rather than silently depending on the host display. The GUI image must not inherit the assumption that headless LiDAR rendering proves GUI rendering works.

4. **Treat video capture failure as separate from replay outcome.** Preserve the replay result and logs even if display startup, GUI rendering, or encoding fails. Persist a clear video status and reason using the existing evidence contract. Container startup failures should be reported as replay infrastructure failures; capture-only failures should not overwrite a valid replay result.

5. **Use a fixed overview for the first viewer.** Configure Gazebo's actual GUI view to keep the robot and enough of the environment visible throughout replay. A fixed overview is preferable to an automatically tracking camera for understanding where the robot is relative to obstacles and restricted areas. Validate the framing against representative failures.

6. **Persist replay job history in the campaign.** Store a versioned job ledger at `manual-reviews/replay-jobs.json`, updating it atomically as jobs change state and each repeat completes. On review-server startup, retain completed history and mark queued or running jobs as interrupted, since their subprocesses cannot be resumed safely. The replay bundles remain beside the ledger under `manual-reviews/case-<id>/...`.

7. **Scope replay history to the selected case attempt.** The jobs endpoint accepts case and attempt filters, and the GUI requests only the selected candidate's jobs. Include each attempt's `sim_duration` from its result bundle in candidate data and show it in the left queue.

## Risks / Trade-offs

- [Risk] Gazebo GUI may not run correctly with Xvfb and Mesa software OpenGL in the current ROS/Gazebo image. → Build a minimal rendering probe early; verify a video can be produced on the target headless server before wiring the reviewer UI. Add a documented GPU runtime only if software rendering is insufficient.
- [Risk] Software rendering can make visual replay slow. → Keep it opt-in, bound replay duration and container resources, and report startup/runtime failures explicitly.
- [Risk] A graphical replay may not reproduce timing-sensitive behavior exactly. → Keep original episode results authoritative, record the original bundle and replay origin, and label the generated output as replay evidence.
- [Risk] Gazebo may open a GUI that renders the world but does not frame the robot usefully. → Validate the default view with representative scenarios and tune the simulator GUI camera configuration as part of the feature.
- [Risk] A cancelled reviewer job could leave a container or encoder running. → Tie container and child-process cleanup to the existing bounded replay job lifecycle and validate cancellation.
- [Risk] A server restart can interrupt a replay and leave a partial bundle. → Persist partial repeat results, mark the job interrupted, and keep already-produced bundle artifacts available for inspection.

## Migration Plan

No data migration is required. Existing bundles remain readable. Existing headless replay behavior remains the default; video-enabled replay uses the new isolated graphical runtime and writes video metadata and the video artifact beside the replay result. The new optional container image/runtime must be built or pulled before this mode is available. Rollback consists of disabling the graphical video replay backend; ordinary simulation and headless replay continue to work.

## Open Questions

- What frame rate, maximum video duration, and container CPU/memory limits keep reviewer-generated clips useful and responsive on the deployment server? Initial values can be tuned after the rendering probe without changing the user-facing workflow.
