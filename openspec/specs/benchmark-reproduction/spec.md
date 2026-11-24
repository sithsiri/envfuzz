# benchmark-reproduction Specification

## Purpose

Compare scenario sampling fairly and preserve repeatable navigation and mapping regressions.

## Requirements

### Requirement: Comparable sampling strategies
Campaigns SHALL support seeded random, stratified, and coverage-guided scenario sampling under a common completed-episode budget.

#### Scenario: Compare strategies on a frozen plan
- **WHEN** multiple strategies run under identical validity rules and budgets
- **THEN** the comparison reports coverage, navigation outcomes, and infrastructure retries per strategy.

### Requirement: Replayable regression corpus
The corpus SHALL group navigation and mapping regressions by a documented behavioral signature and link each representative to its original and replayed runs.

#### Scenario: Simplify a regression fixture
- **WHEN** clutter is removed from a stored fixture and the same navigation or mapping regression recurs at the configured replay rate
- **THEN** the smaller fixture and measured reproduction rate are saved with links to the original and repeat bundles.

### Requirement: Measured replay stability
The benchmark SHALL record episode configuration and report replay agreement across documented CPU profiles.

#### Scenario: Replay under CPU load
- **WHEN** the same scenarios run on an unloaded and a constrained host profile
- **THEN** the report states the agreement rate, real-time factors, and any observed change in outcome.

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
