## Purpose

Provides a reproducible baseline environment in which a complete robot episode can run without a GPU, display server, or interactive operator.

## ADDED Requirements

### Requirement: The baseline simulation SHALL run headlessly on CPU
The distributable runtime SHALL include the pinned ROS 2 Jazzy and Gazebo Harmonic dependencies, baseline apartment world, circular differential-drive robot, and required navigation and SLAM components without requiring NVIDIA, X11, RViz, or DISPLAY.

#### Scenario: CPU-only startup
- **WHEN** the image is run on an amd64 machine with no GPU and DISPLAY unset
- **THEN** the simulator and robot stack SHALL start successfully in headless mode

#### Scenario: Version inspection
- **WHEN** a user runs the diagnostic command in the image
- **THEN** the command SHALL print the pinned versions of the runtime components

### Requirement: The baseline world SHALL expose required robot and metadata interfaces
The simulation SHALL provide a small apartment world, robot motion, 2D LiDAR, odometry, ground-truth pose, restricted-zone metadata, and default mission goals through documented interfaces.

#### Scenario: Baseline mission
- **WHEN** the default goal is sent in the unfuzzed world
- **THEN** the robot SHALL reach the goal and ground-truth pose data SHALL be available for the episode

### Requirement: Simulation logic SHALL use simulation time
The launch configuration SHALL bridge the simulation clock and configure all participating ROS nodes to use it.

#### Scenario: Variable host speed
- **WHEN** the same mission is run at different achieved real-time factors
- **THEN** time-based behavior and outcome evaluation SHALL be based on simulation time rather than wall-clock speed
