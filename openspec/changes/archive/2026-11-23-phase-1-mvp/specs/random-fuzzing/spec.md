## Purpose

Lets users search the Phase 1 scenario space reproducibly by generating valid environment changes and executing them within a finite sequential budget.

## ADDED Requirements

### Requirement: Fuzzing SHALL be deterministic from its seed
The fuzzer SHALL accept a seed and generate the same candidate sequence, validation decisions, and ordering of completed findings for the same inputs and runtime configuration.

#### Scenario: Repeated seeded run
- **WHEN** fuzzing is invoked twice with the same seed and budget
- **THEN** it SHALL generate equivalent valid scenarios in the same order

### Requirement: Invalid candidates SHALL not consume simulation budget
The fuzzer SHALL generate random objects and placements within configured constraints, discard invalid candidates, and count only valid scenarios that are actually simulated toward the episode budget.

#### Scenario: Rejected candidate
- **WHEN** a generated candidate fails scenario validation
- **THEN** it SHALL be recorded as rejected or logged for diagnosis and SHALL not decrement the simulation budget

### Requirement: Fuzzing SHALL persist and summarize findings
The fuzzer SHALL run valid scenarios sequentially, append each result to the configured JSONL output, and print a sorted summary containing score and tiers hit.

#### Scenario: Budget completion
- **WHEN** the requested valid-simulation budget is reached
- **THEN** the command SHALL exit successfully with persisted results and a summary sorted by descending score
