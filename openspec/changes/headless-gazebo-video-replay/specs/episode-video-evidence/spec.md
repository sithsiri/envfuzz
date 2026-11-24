# Spec Delta

## MODIFIED Requirements

### Requirement: Optional episode video capture
The system SHALL allow operators to request video for an individual episode, campaign, or replay selection while preserving normal execution when video is disabled.

#### Scenario: Video is requested for a live episode
- **WHEN** an operator enables recording
- **THEN** the episode bundle includes the video artifact and a bundle-relative reference to it

#### Scenario: Video is requested for a replay
- **WHEN** an operator enables video for a selected replay
- **THEN** the replay bundle includes a video of Gazebo's rendered simulator view and a bundle-relative reference to it
- **AND** the graphical view marks the robot's start and each navigation goal with distinct colors and shapes

#### Scenario: A replay fails before the robot moves
- **WHEN** navigation aborts shortly after Gazebo starts
- **THEN** capture begins when the Gazebo GUI window is ready so the clip includes the rendered scenario and its start and goal markers
- **AND** the replay result retains the navigation failure independently from video availability

#### Scenario: Video is disabled
- **WHEN** a normal run or campaign executes
- **THEN** behavior and bundle generation remain unchanged and no video artifact is required

### Requirement: Request replay evidence from the review GUI
The system SHALL allow an operator to start replay evidence generation for a selected candidate from the local review interface.

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

## ADDED Requirements

### Requirement: Isolated graphical replay on a headless host
The system SHALL generate requested Gazebo replay video without requiring or using a host display session.

#### Scenario: Video replay runs without a host display
- **GIVEN** the host has no `DISPLAY` and may have other display workloads
- **WHEN** an operator requests video for a selected replay
- **THEN** Gazebo's graphical replay and capture use an isolated display environment
- **AND** no other host display session is required, captured, or interrupted

### Requirement: Preserve replay results when video capture fails
Failure to render or record replay video SHALL be reported independently and SHALL NOT replace or erase the replay's simulation result.

#### Scenario: Graphical capture is unavailable
- **WHEN** the replay completes but its graphical display or video encoder fails
- **THEN** the replay retains its result and diagnostic artifacts
- **AND** the video metadata reports an unavailable or failed status and the reason
