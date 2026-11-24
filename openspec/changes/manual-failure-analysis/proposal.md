# Proposal

## Why

EnvFuzz now produces durable run bundles and campaign summaries, but notable
failures remain difficult to find and classify consistently. The project needs
a human-review workflow that turns timeout and mission-failure candidates into
categorized evidence, and it needs optional recordings for the strongest cases
to support final presentations.

## What Changes

- Export failed campaign episodes into a filterable review queue that links to
  the original bundle, diagnostics, logs, telemetry, and replay attempts.
- Add a versioned manual-review record with failure type, root-cause category,
  confidence, reproducibility, evidence, and presentation-candidate fields.
- Generate a campaign-level report that separates behavior failures,
  infrastructure failures, and replay outcomes.
- Preserve an evidence archive for each selected case, including diagnostics
  and optional episode video.
- Add optional episode video recording for interesting live runs and replayed
  cases without making video mandatory for normal execution.
- Publish a separate human-use guide covering candidate selection, diagnosis,
  classification, replay verification, and presentation use.

## Capabilities

### New Capabilities

- `failure-analysis-review`: Candidate discovery, review status, structured
  root-cause classification, and campaign-level manual-analysis reporting.
- `episode-video-evidence`: Optional simulation episode recording that remains
  linked to the exact bundle, runtime, scenario, and replay configuration.

### Modified Capabilities

None.

## Impact

- Adds review and export functions to the Python analysis/report modules and
  CLI.
- Extends static campaign reports and CSV/JSON campaign summaries.
- Adds versioned JSON review records and a human-readable guide under `docs/`.
- May extend Gazebo launch and worker configuration for optional video capture.
- Keeps bundle compatibility and normal campaign execution unchanged.
