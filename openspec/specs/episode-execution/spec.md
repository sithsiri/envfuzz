# episode-execution Specification

## Purpose

Defines the lifecycle and durable outputs for running one validated scenario as an isolated, observable robot episode.

## Requirements

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

### Requirement: Navigation goals are checked against the active map before dispatch
The episode runner SHALL verify that each requested goal lies within the current navigation map bounds before sending it to the navigation stack. An out-of-bounds goal SHALL not be dispatched or silently moved to a different coordinate.

#### Scenario: Goal lies inside the active map
- **WHEN** the current navigation map and goal transform are available and the requested goal is inside the map bounds
- **THEN** the runner dispatches the requested goal unchanged

#### Scenario: Goal lies outside the active map
- **WHEN** the current navigation map is available and the requested goal is outside its bounds
- **THEN** the runner does not dispatch the goal
- **AND** the result records the requested goal, map bounds, and a stable map-bounds failure reason

#### Scenario: Ordered mission reaches a later goal
- **WHEN** the runner is about to dispatch each goal in an ordered mission
- **THEN** it checks that goal against the current map bounds before dispatch
- **AND** a map that changed after a previous goal does not permit an unchecked dispatch

#### Scenario: Map bounds or goal transform are unavailable
- **WHEN** the runner cannot obtain usable map bounds or transform the goal into the map frame before the existing navigation startup deadline
- **THEN** it does not dispatch the goal
- **AND** the result records which prerequisite was unavailable
