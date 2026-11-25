# Tasks

## 1. Define map-bound coverage checks

- [x] 1.1 Add a focused world-to-map bounds utility that respects map origin, orientation, resolution, and edge semantics; unit tests cover in-bounds, out-of-bounds, rotated maps, and exact cell boundaries.
- [x] 1.2 Document the coordinate frame and cell-extent convention used by runtime goal checks; verify the documentation matches the utility tests.

## 2. Guard runtime goal dispatch

- [x] 2.1 Gate each ordered navigation goal on current map bounds and a valid goal transform, using the existing bounded startup deadline; unit tests verify no out-of-bounds goal reaches the action client.
- [x] 2.2 Record a stable map-bounds failure reason with the requested goal and observed bounds while preserving existing result compatibility; tests cover unavailable map and transform prerequisites.
- [x] 2.3 Document the new result reason and expected behavior for an out-of-map mission; verify the user-facing docs distinguish coverage failure from planner no-path failure.

## 3. Verify end-to-end behavior

- [x] 3.1 Add a simulator-backed regression using a valid scenario whose goal is outside the generated active map; verify the runner stops before Nav2 goal dispatch and preserves diagnostics in the bundle.
- [x] 3.2 Run representative in-bounds single- and multi-goal missions; verify each unchanged goal is dispatched only after passing the runtime bounds check.
