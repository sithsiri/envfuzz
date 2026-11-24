# Spec Delta

## Purpose

Provide optional recorded evidence for notable episodes without making video
capture or a display server a requirement for campaigns.

## ADDED Requirements

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
