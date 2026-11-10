## Purpose

Measures deviations from an unfuzzed baseline using explicit, configurable detectors and produces explainable severity-tiered scores.

## ADDED Requirements

### Requirement: Baseline calibration SHALL derive scoring thresholds
The system SHALL run the unfuzzed mission repeatedly and store metric distributions and explainable thresholds, and SHALL prevent or loudly warn on scoring without a baseline.

#### Scenario: Baseline creation
- **WHEN** the calibration command completes N valid baseline runs
- **THEN** it SHALL write a versioned baseline containing distributions and the derived thresholds

#### Scenario: Missing baseline
- **WHEN** a scored run is requested without a baseline
- **THEN** the command SHALL refuse to score or emit a clearly visible warning according to the configured policy

### Requirement: The oracle SHALL detect Phase 1 deviation tiers
The oracle SHALL record ground-truth pose, SLAM pose, and mission status and SHALL detect stuck behavior, baseline-relative SLAM pose error, and restricted-area entry as separate events.

#### Scenario: Stuck detector
- **WHEN** ground-truth displacement remains below the configured epsilon for the configured simulation-time window while the mission is incomplete and the baseline completed
- **THEN** the oracle SHALL emit a T1 event

#### Scenario: Pose error detector
- **WHEN** aligned SLAM-to-world pose error exceeds its baseline-derived threshold for the configured duration
- **THEN** the oracle SHALL emit a T2 event with timing and measured error

#### Scenario: Restricted-area detector
- **WHEN** the ground-truth robot footprint enters a configured restricted polygon
- **THEN** the oracle SHALL emit a T4 event with the zone identity and event interval

### Requirement: Scores SHALL be explainable and configurable
The oracle SHALL apply configured tier points, record all detected events, and report the highest tier reached as the scenario score unless a future configuration explicitly selects another aggregation.

#### Scenario: Multiple events
- **WHEN** an episode triggers multiple tiers
- **THEN** the result SHALL retain every event and SHALL report the configured aggregate score and tiers hit
