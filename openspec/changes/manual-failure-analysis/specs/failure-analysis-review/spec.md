# Spec Delta

## Purpose

Turn campaign failures into a traceable manual-analysis queue with stable
classification records and links to reproducible episode evidence.

## ADDED Requirements

### Requirement: Export behavior failure candidates
The system SHALL export timeout and mission-failure episodes from campaign
results into a review queue without treating infrastructure outcomes as confirmed behavior failures.

#### Scenario: A tuned campaign contains mixed outcomes
- **WHEN** an operator exports the campaign review queue
- **THEN** every behavior timeout and mission failure has a case, attempt,
  bundle, strategy, layout, complexity, and outcome field
- **AND** watchdog, launch, and simulator outcomes remain separately classified

#### Scenario: An episode has no review record
- **WHEN** the review queue is exported
- **THEN** the episode is assigned an unreviewed status without changing its result

### Requirement: Record manual root-cause classifications
The system SHALL store a versioned human analysis record for each reviewed episode.

#### Scenario: A reviewer diagnoses a case
- **WHEN** the reviewer saves an analysis
- **THEN** the record includes status, failure category, root-cause category,
  confidence, reproducibility, evidence summary, and presentation-candidate fields

#### Scenario: The recorded classification is incomplete
- **WHEN** a required root-cause field is missing
- **THEN** the system rejects the update and leaves the prior record unchanged

### Requirement: Provide a campaign review report
The system SHALL present review candidates and their analysis status in a
human-readable campaign report.

#### Scenario: An operator inspects an interesting signature
- **WHEN** the operator opens the review report
- **THEN** the report links the candidate to its bundle, diagnostics, telemetry,
  logs, replay attempts, and manual-analysis record

#### Scenario: An operator filters candidates
- **WHEN** the operator filters by outcome, category, confidence, or review status
- **THEN** only matching candidates are shown

### Requirement: Keep infrastructure and replay outcomes separate
The system SHALL distinguish infrastructure failures and replay reproducibility
from confirmed navigation behavior failures.

#### Scenario: A watchdog retry later succeeds
- **WHEN** the campaign reconciles the retry
- **THEN** the watchdog is retained as infrastructure attempt evidence and is
  not promoted into the behavior-failure queue

#### Scenario: A replay does not reproduce a candidate
- **WHEN** repeated replay outcomes do not match the candidate signature
- **THEN** the review record reports the candidate as not reproducible and does
  not automatically classify it as a confirmed regression
