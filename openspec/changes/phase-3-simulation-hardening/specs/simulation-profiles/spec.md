# Simulation profiles

## Purpose

Make indoor robot and navigation configurations reproducible and comparable across simulated episodes.

## ADDED Requirements

### Requirement: Versioned robot and navigation profiles
The simulator SHALL resolve versioned robot definitions and allowlisted navigation configurations. Bundles, challenges and baselines SHALL identify the resolved robot, backend, sensor and timing settings.

#### Scenario: Profile changes
- **WHEN** an organizer changes the robot or navigation backend
- **THEN** the evaluator requires a matching baseline and records the resolved settings for replay

#### Scenario: Backend comparison
- **WHEN** the same frozen suite is run with the default SLAM backend, an alternative SLAM backend and map-based localization
- **THEN** the report compares outcomes, infrastructure failures and timing under the same scenario budget using matching baselines

### Requirement: Optional sensor capabilities
Profiles SHALL support simulated contact and cliff observations, IMU and odometry observations, and an optional GPU-gated camera path.

#### Scenario: CPU-only default
- **WHEN** an existing scenario runs on a headless CPU worker
- **THEN** it uses the existing robot and planar LiDAR defaults without requiring camera hardware

#### Scenario: GPU camera profile
- **WHEN** an organizer selects the RTAB-Map camera profile
- **THEN** the runtime verifies that an NVIDIA GPU is exposed before launch, starts the Gazebo RGB-D camera and RTAB-Map backend, and records the resolved camera settings in the bundle

#### Scenario: Sensor profile replay
- **WHEN** a frozen low-profile vacuum fixture crosses a friction patch or approaches a simulated ledge
- **THEN** the bundle records the enabled sensor observations and the profile required to reproduce the test
