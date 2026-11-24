# scenario-variants Specification

## Purpose

Describe repeatable indoor environment variations and apply scheduled edits during a simulated mission.

## Requirements

### Requirement: Budgeted seeded edits
The scenario editor SHALL support seeded object movement, rotation, scaling, clutter changes, and door state changes under declared edit costs and validity constraints.

#### Scenario: Edit exceeds the configured budget
- **WHEN** an edit would exceed the permitted cost or create invalid geometry
- **THEN** validation rejects it and identifies the offending edit and constraint.

### Requirement: Simulation-time events and compatibility
The scenario format SHALL accept timed environment edits and continue to load existing Phase 1 static scenarios.

#### Scenario: Move a chair during a mission
- **WHEN** a valid chair move is scheduled for a simulation timestamp
- **THEN** it is applied once at that simulation step, recorded in the bundle, and reflected in subsequent LiDAR observations.

### Requirement: Validated indoor surface and floor models
The scenario format SHALL accept versioned simulation properties for surface response and floor friction. Seeded edits SHALL respect the same validation and budget rules as other environment changes. Surface response SHALL be verified with the deterministic offline reference model; the native Gazebo LiDAR remains the navigation observation source, and material fixtures SHALL NOT require live transmission or reflectivity-based dropout behavior from Gazebo.

#### Scenario: Run a fixed surface fixture
- **WHEN** a documented synthetic fixture uses transparent, reflective, dark or thin surfaces
- **THEN** the offline reference probe records the configured synthetic observation and verifies the expected response against its documented fixture tolerance, while live navigation continues to use native Gazebo ranges

#### Scenario: Load an earlier scenario
- **WHEN** an existing scenario omits material and friction properties
- **THEN** the compiler uses the existing surface and floor defaults

### Requirement: Scripted actors
The scenario format SHALL validate actor paths against declared world and trajectory limits and execute accepted paths on simulation time.

#### Scenario: Replay a scripted actor
- **WHEN** a valid actor trajectory is replayed under its recorded profile
- **THEN** actor poses match the declared simulation times within fixture tolerance and the actor is represented in simulated LiDAR observations
