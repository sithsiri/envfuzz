## Purpose

Defines the portable scenario contract and deterministic preprocessing needed to turn environment modifications into safe, reproducible simulation inputs.

## ADDED Requirements

### Requirement: Scenarios SHALL be versioned and validated
The system SHALL load YAML scenarios containing a version, seed, object placements, and challenge metadata, and SHALL reject malformed or unsupported scenarios with specific validation errors.

#### Scenario: Valid scenario round trip
- **WHEN** a valid scenario is loaded and serialized canonically
- **THEN** the serialized representation SHALL be stable and reload to an equivalent scenario with a reproducible hash

#### Scenario: Invalid scenario
- **WHEN** a scenario has an unknown version, missing required field, or invalid geometry
- **THEN** validation SHALL fail without producing a runnable world and SHALL identify the offending field or rule

### Requirement: Scenario validity SHALL be checked before simulation
The system SHALL reject scenarios whose objects overlap walls or one another, exceed world bounds, violate start/goal clearance, or violate configured restricted-zone rules.

#### Scenario: Physically invalid placement
- **WHEN** an object intersects another object or a wall
- **THEN** the scenario SHALL be rejected with a violation describing the entities and violated constraint

#### Scenario: Valid placement
- **WHEN** all configured geometric and challenge constraints pass
- **THEN** the scenario SHALL be accepted for compilation

### Requirement: Scenario compilation SHALL be deterministic
The system SHALL compile a base world and a valid scenario into a concrete SDF world using only the supported static-placement features of Phase 1.

#### Scenario: Repeated compilation
- **WHEN** the same base world, scenario, and seed are compiled more than once
- **THEN** each generated SDF SHALL be byte-identical

#### Scenario: Unsupported runtime event
- **WHEN** a scenario requests a dynamic event not supported by Phase 1
- **THEN** compilation SHALL reject it explicitly rather than silently ignoring it
