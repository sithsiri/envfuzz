# Classroom evaluation delta

## ADDED Requirements

### Requirement: Isolated sensor-fault verification
Organizers SHALL be able to evaluate fixed synthetic sensor-fault traces through offline test fixtures and the existing scoring contract. Submitted data SHALL be validated before evaluation, and participant inputs SHALL have no direct sensor-topic access.

#### Scenario: Offline fixture
- **WHEN** an organizer replays a versioned synthetic observation fixture
- **THEN** the report records the fixture identity and compares the observed oracle outcome with its expected tier or tolerance

#### Scenario: Unsupported submission content
- **WHEN** a submission exceeds input bounds or contains paths, executable content or unsupported configuration
- **THEN** validation rejects it before starting evaluation and identifies the violated rule

### Requirement: Documented classroom workflow
The project SHALL provide participant and organizer guides for the supported benchmark packages, scoring and local checks, matched calibration, reference validation, authoritative evaluation and fairness controls.

#### Scenario: Verify a published benchmark
- **WHEN** an organizer follows the guide with a supported fixture
- **THEN** they can reproduce its calibration and verification report using the documented execution limits
