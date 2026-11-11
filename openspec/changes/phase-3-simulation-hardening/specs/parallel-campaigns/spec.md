# Parallel campaigns delta

## ADDED Requirements

### Requirement: Durable multi-host evaluation
Approved workers on multiple hosts SHALL claim versioned campaign jobs under a bounded lease and execute the existing resource-limited episode workflow. Attempts SHALL preserve the current bundle format and evaluation provenance.

#### Scenario: Worker disappears after claiming a job
- **WHEN** its lease expires before an authoritative result is committed
- **THEN** an eligible worker can retry within the configured budget and the attempt remains visible in diagnostics

#### Scenario: Two attempts report the same job
- **WHEN** a recovered attempt and an earlier attempt both report a result
- **THEN** the evaluator commits at most one authoritative result and preserves attempt provenance for audit
