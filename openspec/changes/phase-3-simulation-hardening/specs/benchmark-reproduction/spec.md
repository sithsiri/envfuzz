# Benchmark reproduction delta

## ADDED Requirements

### Requirement: Evidence-based regression gates
The benchmark SHALL track replay agreement, infrastructure failures, real-time factor and environment metadata separately. A fixture SHALL qualify for required CI only after meeting a declared agreement threshold in repeated verification runs.

#### Scenario: An unstable fixture is replayed
- **WHEN** a candidate falls below its declared replay agreement threshold
- **THEN** the report keeps the candidate available for diagnosis and records its measured agreement separately from the required CI corpus

### Requirement: Comparable coverage experiments
When an additional sampling strategy is evaluated, the benchmark SHALL compare it with Phase 2 strategies using frozen fixtures, seeds, validity rules and episode budgets.

#### Scenario: Compare an experimental sampler
- **WHEN** an experimental strategy is evaluated
- **THEN** the report compares coverage, valid episodes, repeatability and compute cost and records whether the evidence supports adoption
