# Classroom evaluation

## Purpose

Evaluate classroom navigation submissions consistently on versioned indoor simulation fixtures.

## ADDED Requirements

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
