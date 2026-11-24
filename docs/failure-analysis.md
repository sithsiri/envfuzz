# Manual failure analysis

This guide explains how to turn campaign failures into classified evidence for
reports and final presentations.

## 1. Export candidates

Export timeout and mission-failure candidates from a completed campaign:

```sh
envfuzz review-export runs/campaign
```

This creates:

- `review-candidates.json` for machine-readable analysis
- `review-candidates.csv` for spreadsheet review

Watchdogs and launch failures are not behavior-failure candidates. They remain
infrastructure evidence and should not be presented as confirmed navigation
failures.

## 2. Start with the strongest evidence

Open the candidate export and prioritize:

- repeated cases across layouts or samplers
- long travel with low completion
- many recovery attempts
- transform waits or Nav2 no-progress messages
- replay candidates that reproduce consistently
- cases with a clear trajectory or map artifact

Open a candidate bundle and inspect `result.json`, `telemetry.jsonl`,
`logs.txt`, `metrics.json`, and `diagnostics.html`.

## 3. Classification

Record a review with `envfuzz review-update`.

```sh
envfuzz review-update runs/campaign \
  --case 378 \
  --attempt 1 \
  --observed-status mission_failed \
  --observed-classification behavior \
  --bundle case-000378/attempt-000001/bundle \
  --review-status confirmed \
  --failure-category planning \
  --root-cause-category blocked_route \
  --confidence high \
  --reproducibility reproducible \
  --evidence-summary "The selected route becomes blocked near the corridor obstacle." \
  --presentation-candidate \
  --video case-000378.mp4
```

### Failure categories

- `planning`: route or goal selection does not complete
- `localization`: pose or map estimate diverges
- `perception`: sensor or map evidence is misleading
- `control`: the robot does not execute a valid plan
- `recovery`: recovery behavior fails or cycles
- `infrastructure`: startup or execution infrastructure is responsible
- `oracle`: a detector or result contract may be wrong
- `scenario`: the scenario or its limits are responsible
- `unknown`: evidence is insufficient

### Root-cause categories

- `blocked_route`
- `localization_drift`
- `sensor_degradation`
- `stale_map`
- `stuck_recovery`
- `startup_stall`
- `resource_contention`
- `incorrect_result_classification`
- `unknown`

Use `high` confidence only when trajectory, telemetry, replay, and logs agree.
Use `medium` for one or two independent signals. Use `low` when the cause is
not established.

## 4. Replay before presentation

Replay the original bundle under identical scenario and runtime settings:

```sh
envfuzz replay \
  runs/campaign/case-000378/attempt-000001/bundle \
  --output runs/manual-review/case-000378/replay
```

Repeat enough times to determine reproducibility. Report a case as
`reproducible` only when repeated behavior matches the original signature.
If replays disagree, keep it `not_reproducible` and do not describe it as a
confirmed environment regression.

## 5. View the campaign

The campaign report can filter review state:

```sh
envfuzz report runs/campaign \
  --outcome timeout \
  --review-status confirmed \
  --failure-category planning \
  --confidence high
```

The report links result bundles, diagnostics, logs, and recorded review state.

## 6. Presentation archive

Mark only clearly explained, replayed cases as presentation candidates. Keep the
original bundle and replay bundles beside the final video and
`manual-reviews.json` so the evidence remains traceable.
