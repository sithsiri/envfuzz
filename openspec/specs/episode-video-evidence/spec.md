# Episode Video Evidence Specification

## Purpose

Provide optional recorded evidence for notable episodes without making video
capture or a display server a requirement for campaigns.

## Requirements

### Requirement: Optional episode video capture
The system SHALL allow operators to request video for an individual episode,
campaign, or replay selection while preserving normal execution when video is disabled.

#### Scenario: Video is requested for a live episode
- **WHEN** an operator enables recording
- **THEN** the episode bundle includes the video artifact and a bundle-relative
  reference to it

#### Scenario: Video is disabled
- **WHEN** a normal run or campaign executes
- **THEN** behavior and bundle generation remain unchanged and no video artifact
  is required

#### Scenario: A replay is recorded
- **WHEN** an operator enables video for a replay
- **THEN** the replay bundle records that it originated from the selected
  original bundle

#### Scenario: Video is requested for a replay
- **WHEN** an operator enables video for a selected replay
- **THEN** the replay bundle includes a video of Gazebo's rendered simulator
  view and a bundle-relative reference to it
- **AND** the graphical view marks the robot's start and each navigation goal
  with distinct colors and shapes
- **AND** those review-only markers are elevated above the robot's planar
  sensing layer
- **AND** their visibility flags exclude them from robot rendering sensors
  while leaving them visible in the Gazebo GUI

#### Scenario: A replay fails before the robot moves
- **WHEN** navigation aborts shortly after Gazebo starts
- **THEN** capture begins when the Gazebo GUI window is ready so the clip
  includes the rendered scenario and its start and goal markers
- **AND** the replay result retains the navigation failure independently from
  video availability

### Requirement: Bind video to episode evidence
Recorded video SHALL identify the exact episode configuration and preserve its
origin alongside the bundle.

#### Scenario: A presentation case is reviewed
- **WHEN** an operator opens a marked presentation candidate
- **THEN** the evidence record identifies the scenario, runtime profile,
  bundle, attempt, replay origin, and video path

#### Scenario: A replay is recorded
- **WHEN** an operator enables video for a replay
- **THEN** the replay bundle records that it originated from the selected
  original bundle

### Requirement: Fail safely without video support
The system SHALL not silently convert video capture failure into a simulation
or navigation result.

#### Scenario: Recording is unavailable
- **WHEN** video capture is requested but the host lacks supported capture
  capability
- **THEN** the episode reports an explicit video-recording status and still
  preserves its simulation result

#### Scenario: Recording stops before episode completion
- **WHEN** the recording process fails during an episode
- **THEN** the bundle reports the recording failure separately and retains every
  other available diagnostic artifact

### Requirement: Request replay evidence from the review GUI
The system SHALL allow an operator to start replay evidence generation for a
selected candidate from the local review interface.

#### Scenario: An operator requests a replay
- **WHEN** the operator starts a replay for a candidate
- **THEN** the reviewer creates a bounded asynchronous replay job
- **AND** the job records queued, running, completed, failed, and cancelled states
- **AND** repeated replay attempts remain separately traceable

#### Scenario: An operator requests replay video
- **WHEN** the operator enables video while starting replay
- **THEN** the replay bundle uses the existing replay-origin and video metadata contracts
- **AND** the interface reports when video is available or unavailable
- **AND** an available video shows Gazebo's rendered simulator view

#### Scenario: An operator retries an interesting replay
- **WHEN** a replay job completes
- **THEN** the interface links the new replay bundle and its result evidence
- **AND** it preserves the original candidate and prior replay bundles

#### Scenario: Replay infrastructure is unavailable
- **WHEN** the local host cannot launch the replay backend
- **THEN** the job reports an explicit infrastructure failure
- **AND** the interface preserves the original bundle and manual-review state

#### Scenario: Replay history survives review server restart
- **WHEN** the review server restarts after replay jobs have been created
- **THEN** completed job records and their artifact links remain available from
  the campaign
- **AND** queued or running jobs are shown as interrupted with any completed
  repeat results retained

#### Scenario: Replay history is shown only for its originating candidate
- **WHEN** an operator views a candidate in the review interface
- **THEN** the replay list contains only jobs for that case and attempt
- **AND** switching candidates refreshes the list for the newly selected case
  and attempt

#### Scenario: Case queue shows simulation duration
- **WHEN** candidate results include a simulation duration
- **THEN** the review queue displays that duration beside the case
- **AND** missing duration values are presented as unknown

### Requirement: Isolated graphical replay on a headless host
The system SHALL generate requested Gazebo replay video without requiring or
using a host display session.

#### Scenario: Video replay runs without a host display
- **GIVEN** the host has no `DISPLAY` and may have other display workloads
- **WHEN** an operator requests video for a selected replay
- **THEN** Gazebo's graphical replay and capture use an isolated display
  environment
- **AND** no other host display session is required, captured, or interrupted

### Requirement: Preserve replay results when video capture fails
Failure to render or record replay video SHALL be reported independently and
SHALL NOT replace or erase the replay's simulation result.

#### Scenario: Graphical capture is unavailable
- **WHEN** the replay completes but its graphical display or video encoder
  fails
- **THEN** the replay retains its result and diagnostic artifacts
- **AND** the video metadata reports an unavailable or failed status and the
  reason
