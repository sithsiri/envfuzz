# Tasks

## 1. Prove isolated Gazebo GUI rendering

- [x] 1.1 Build a minimal container probe that starts Gazebo's GUI under an isolated virtual X display with Mesa software rendering and encodes a short clip; verify the clip opens and shows the robot moving on the target headless server.
- [x] 1.2 Software rendering worked in the probe, so a GPU/container runtime path was not needed.

## 2. Package the video replay runtime

- [x] 2.1 Add a dedicated graphical replay image/runtime with Gazebo GUI, virtual display, encoder, and bounded process cleanup; verify the image builds and a bounded replay leaves no container or child process running.
- [x] 2.2 Configure the Gazebo GUI overview to frame scenario bounds so representative scenes keep the robot and relevant environment visible; verify sample clips show motion and spatial context.
- [x] 2.3 Document the optional runtime build, resource requirements, and headless server invocation; verify the documented setup commands match the image and runtime configuration.
- [x] 2.4 Add graphical-only start and goal markers; verify they appear in video worlds and do not alter headless world compilation.

## 3. Connect replay and reviewer video controls

- [x] 3.1 Route video-enabled replay through the isolated graphical backend while keeping replay without video on the existing headless path; verify command-level tests cover both paths and preserve replay-origin metadata.
- [x] 3.2 Persist video availability, artifact path, and startup/render/encoder failure reason independently from replay results; verify tests retain successful or failed simulation results when capture fails.
- [x] 3.3 Update the review interface to request and display the graphical replay video and its status; verify UI tests cover available, unavailable, and failed video states without changing original review data.
- [x] 3.4 Document the reviewer workflow for generating video on a headless host; verify the documented command and UI steps agree with the implemented replay backend.
- [x] 3.5 Start capture when the Gazebo GUI window is ready so fast navigation failures still include the rendered scene and markers; verify a short-failure replay retains its result and readable video.

## 4. Verify end-to-end isolation and evidence

- [x] 4.1 Run an end-to-end video replay with host `DISPLAY` unset and another display workload active; verify Gazebo video is produced in the replay bundle and the other display remains unaffected.
- [x] 4.2 Verify ordinary headless episodes and replays remain usable without graphical packages or a display, and that generated videos link to the original case and replay result.
