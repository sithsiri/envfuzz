# Verify the navigation benchmark

## Why

The mission driver completed two headless Gazebo runs in an existing ROS 2 container. The packaged CLI and complete benchmark still need verification in a fresh environment before the class can use the results to compare sampling methods.

## What Changes

- Verify the CPU-only container from a clean build and exercise the public run, replay, report, baseline, and campaign commands.
- Freeze a pilot campaign and compare uniform and stratified sampling under equal behavior-episode budgets.
- Report navigation outcome counts, repeatable cases, and discovery curves from saved campaign artifacts.

## Capabilities

### New Capabilities

- `campaign-analysis` — Compare campaign outcomes and trace each counted case to its scenario, telemetry, and replay.

### Modified Capabilities

## Impact

The change uses the existing Docker, mission runner, campaign, and report paths. The class will receive the frozen plan, run summaries, and a reproducible analysis. The Phase 1 change continues to track its original simulator acceptance requirements.
