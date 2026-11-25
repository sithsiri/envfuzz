# Proposal

## Why

Nav2 can abort immediately when a requested goal falls outside the active navigation map, leaving a confusing behavior failure and an uninformative replay. Episodes need to check map coverage before dispatching each goal so invalid map extent is reported deterministically and the requested mission is never silently changed.

## What Changes

- Check each navigation goal against the active map bounds in the navigation frame before sending it to Nav2.
- Do not dispatch an out-of-bounds goal or silently clamp it; record the requested goal, active bounds, and a stable reason in the episode evidence.
- Apply the check to every goal in an ordered mission, using the current map bounds at dispatch time.
- Preserve existing behavior for goals inside the active map and keep simulator-free scenario validation deterministic.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `episode-execution`: Require navigation goals to be checked against the active map before dispatch and produce a distinct diagnostic when coverage is insufficient.

## Impact

Likely affected areas include the mission orchestration and navigation action dispatch in `src/envfuzz/mission.py`, result classification and bundle evidence in `src/envfuzz/results.py`, and simulator-backed integration tests. The check depends on runtime map metadata and transforms; it does not add ROS or map-server dependencies to simulator-free scenario contracts.
