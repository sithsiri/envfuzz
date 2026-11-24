# classroom-evaluation Specification

## Purpose

Evaluate classroom navigation submissions consistently on versioned indoor simulation fixtures.

## Requirements

### Requirement: Versioned benchmark packages
A classroom package SHALL define the world, robot, mission, evaluation criteria, allowed environment edits, placement budgets, and execution limits.

#### Scenario: Evaluate multiple layout families
- **WHEN** the organizer publishes the classroom suite
- **THEN** it includes an open room, a corridor layout, and a multi-room layout with separate baselines, documented difficulty, and organizer reference fixtures.

### Requirement: Data-only validation
The checker SHALL validate submission schema, size, object counts, and permitted edits before starting an evaluation.

#### Scenario: A bundle contains an executable or unsupported file
- **WHEN** a submission contains a file outside the accepted data format
- **THEN** the checker rejects it before loading or running a simulator.

### Requirement: Organizer-verified results
The organizer SHALL rerun validated scenarios in resource-limited containers with networking disabled and read-only inputs and publish results under a documented repeat policy.

#### Scenario: Local and organizer results disagree
- **WHEN** a submitted local result differs from the organizer's reruns
- **THEN** the published scoreboard uses the verified outcome and preserves the evaluated configuration and repeat evidence.

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
