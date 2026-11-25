# Spec Delta

## ADDED Requirements

### Requirement: Navigation goals are checked against the active map before dispatch
The episode runner SHALL verify that each requested goal lies within the current navigation map bounds before sending it to the navigation stack. An out-of-bounds goal SHALL not be dispatched or silently moved to a different coordinate.

#### Scenario: Goal lies inside the active map
- **WHEN** the current navigation map and goal transform are available and the requested goal is inside the map bounds
- **THEN** the runner dispatches the requested goal unchanged

#### Scenario: Goal lies outside the active map
- **WHEN** the current navigation map is available and the requested goal is outside its bounds
- **THEN** the runner does not dispatch the goal
- **AND** the result records the requested goal, map bounds, and a stable map-bounds failure reason

#### Scenario: Ordered mission reaches a later goal
- **WHEN** the runner is about to dispatch each goal in an ordered mission
- **THEN** it checks that goal against the current map bounds before dispatch
- **AND** a map that changed after a previous goal does not permit an unchecked dispatch

#### Scenario: Map bounds or goal transform are unavailable
- **WHEN** the runner cannot obtain usable map bounds or transform the goal into the map frame before the existing navigation startup deadline
- **THEN** it does not dispatch the goal
- **AND** the result records which prerequisite was unavailable
