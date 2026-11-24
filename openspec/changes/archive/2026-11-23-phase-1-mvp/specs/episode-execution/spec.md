## Purpose

Defines the lifecycle and durable outputs for running one validated scenario as an isolated, observable robot episode.

## ADDED Requirements

### Requirement: An episode SHALL have bounded and explicit termination
The runner SHALL terminate on mission completion, simulation-time timeout, or a configurable wall-clock watchdog, and SHALL document distinct exit statuses for robot outcomes and infrastructure failures.

#### Scenario: Mission completion
- **WHEN** the robot reaches the configured goal
- **THEN** the runner SHALL stop the episode and record successful mission completion

#### Scenario: Watchdog timeout
- **WHEN** the wall-clock watchdog expires before normal termination
- **THEN** the runner SHALL terminate the episode, classify it as an infrastructure or timeout condition as configured, and record the reason

### Requirement: Episode cleanup SHALL be reliable
The runner SHALL tear down every child process and leave no episode-owned process running after normal exit, crash, or Ctrl-C.

#### Scenario: Interrupted run
- **WHEN** the user interrupts an active episode
- **THEN** all child processes SHALL be terminated and the output SHALL identify the interruption

### Requirement: Each episode SHALL produce a versioned result bundle
The runner SHALL write the input scenario, compiled world, structured result, runtime versions, logs, timings, achieved real-time factor, and optional rosbag output to the requested run directory.

#### Scenario: Completed bundle
- **WHEN** an episode ends
- **THEN** the bundle SHALL contain the required files and a result schema version, regardless of whether the robot found a scored event

#### Scenario: Simulator crash
- **WHEN** the simulator or launch fails
- **THEN** the result SHALL classify the failure separately from a robot behavior outcome and preserve available diagnostics
