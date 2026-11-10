# Campaign analysis

## Purpose

Compare navigation sampling methods under equal budgets and preserve the evidence behind each outcome.

## ADDED Requirements

### Requirement: Compare equal episode budgets

The analysis SHALL report per-method navigation outcome counts and cumulative distinct cases at the same completed behavior-episode budget.

#### Scenario: Campaign includes an infrastructure retry
- **WHEN** a campaign contains a watchdog or launch failure followed by a completed behavior episode
- **THEN** the analysis counts the behavior episode toward the budget and reports infrastructure attempts separately.

#### Scenario: One method has fewer completed episodes
- **WHEN** the available behavior-episode counts differ
- **THEN** the analysis compares methods through the common budget and reports the observed counts and any shortfall.

### Requirement: Preserve evidence for distinct cases

The analysis SHALL record the case definition, scenario identity, source bundle, and replay outcomes for each counted failure.

#### Scenario: The same behavior recurs in one layout region
- **WHEN** multiple failures share the frozen case signature
- **THEN** the discovery count increases once and the analysis retains links to all contributing bundles.

#### Scenario: A candidate is replayed
- **WHEN** a candidate has the prescribed replay attempts
- **THEN** the analysis reports how many reproduced its signature and identifies the associated run bundles.
