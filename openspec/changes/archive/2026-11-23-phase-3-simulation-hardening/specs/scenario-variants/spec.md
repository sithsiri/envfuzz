# Scenario variants delta

## ADDED Requirements

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
