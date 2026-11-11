# Scenario variants

## Purpose

Describe repeatable indoor environment variations and apply scheduled edits during a simulated mission.

## ADDED Requirements

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
