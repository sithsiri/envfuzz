# Design

## Context

See proposal.md for motivation. The mission runner sends goals through the ROS navigation stack, while the usable map extent and map-to-navigation transform are available only at runtime. Existing scenario validation checks authored geometry against scenario bounds, which does not guarantee the live navigation map covers those coordinates.

## Goals / Non-Goals

**Goals:**

- Prevent an invalid map extent from being reported only as a generic planner abort.
- Preserve the authored mission by never rewriting a goal coordinate to fit the map.
- Keep the preflight check in simulator-backed episode orchestration; scenario parsing and compilation remain ROS-independent.

**Non-Goals:**

- Guarantee that every in-bounds goal is reachable or collision-free.
- Change the navigation map-generation algorithm or expand maps dynamically as a side effect of goal checking.
- Change scenario geometry bounds or mission semantics.

## Decisions

1. **Check the active map at each goal dispatch.** Use the current map metadata and the existing transform path to express the requested goal in the map frame. Check its coordinates against the map's actual extent, including origin and orientation, before dispatching. A check against scenario world limits alone is insufficient because the map may cover less area.

2. **Fail explicitly instead of clamping.** If a goal falls outside the available map, end the mission with a stable diagnostic that includes the requested target and map extent. Clamping can silently turn a requested navigation task into a different task and make fuzzing results incomparable. An in-bounds goal continues through the existing navigation path unchanged.

3. **Bound map-readiness and transform waits.** Reuse the episode's existing navigation startup deadline. If bounds or the required transform do not become available within that deadline, stop before goal dispatch and record the missing prerequisite rather than waiting indefinitely.

4. **Recheck ordered goals immediately before dispatch.** Map extent can evolve during a mission. Validate every next goal against current metadata rather than relying only on a check performed once at startup.

## Risks / Trade-offs

- [Risk] Map messages may describe cell extents with resolution and rotated origins, making edge checks inconsistent. → Centralize the world-to-map conversion and define the valid extent using the map's cell geometry; cover rotated origins and exact edges with unit tests.
- [Risk] A map that would eventually expand to include a target may not include it at the dispatch check. → Wait for the established map readiness condition, then report the bounds available at the deadline; do not guess or alter the requested target.
- [Risk] A newly explicit map-bounds result may affect failure classification consumers. → Preserve the existing result schema where possible and add a stable reason/evidence field without conflating it with a planner no-path result.

## Migration Plan

No stored campaign migration is required. Existing result bundles remain readable. New episodes emit the explicit map-bounds diagnostic when the guard prevents goal dispatch.
