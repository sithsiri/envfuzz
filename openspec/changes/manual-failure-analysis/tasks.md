# Tasks

## 1. Add failure review contracts

- [x] 1.1 Add a versioned manual-review JSON schema for observed outcome,
  failure category, root-cause category, confidence, reproducibility,
  evidence summary, presentation candidate, and video path; verify valid and
  malformed records with unit tests.
- [x] 1.2 Export authoritative timeout and mission-failure episodes as CSV and
  JSON candidate queues; verify watchdog and launch outcomes remain excluded
  from behavior-failure candidates.
- [x] 1.3 Add CLI commands or options for exporting the review queue and reading
  or updating manual-review records; verify documented commands with CLI tests.

## 2. Build the human review report

- [x] 2.1 Extend the campaign report to show review status, outcome, category,
  confidence, replay reproducibility, and bundle/diagnostic/log links; verify
  filters and links with report tests.
- [x] 2.2 Preserve manual-review state separately from result, metrics, claim,
  and replay artifacts; verify updating a review record does not change bundle
  hashes or campaign results.
- [x] 2.3 Add [failure-analysis.md](../../../docs/failure-analysis.md) with
  candidate selection, taxonomy, confidence rules, replay verification,
  presentation selection, commands, and example workflow; verify every command
  against the CLI and documentation checks.

## 3. Add optional episode video evidence

- [x] 3.1 Add opt-in video capture configuration for live episodes and replays;
  verify the default run remains unchanged and no video artifact is required.
- [x] 3.2 Record a bundle-relative video reference and explicit recording status;
  verify capture success and capture-failure scenarios in simulator-free tests.
- [x] 3.3 Add an evidence archive export for marked presentation candidates;
  verify it includes the original/replay bundle identity, diagnostics,
  manual-review record, and video reference.

## 4. Integrate and validate

- [x] 4.1 Add end-to-end simulator-free coverage for queue export, report
  generation, review updates, replay evidence, and presentation archive output.
- [ ] 4.2 Run existing unit and CLI suites plus the focused new suite; verify
  existing campaign, replay, bundle, and report behavior remains compatible.
- [x] 4.3 Validate the OpenSpec change with `openspec validate
  manual-failure-analysis --strict`; verify all planning artifacts are complete
  and the change is ready for implementation.
