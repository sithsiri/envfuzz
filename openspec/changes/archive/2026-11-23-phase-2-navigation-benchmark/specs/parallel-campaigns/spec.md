# Parallel campaigns

## Purpose

Run larger experiments with isolated workers, durable progress, and traceable results.

## ADDED Requirements

### Requirement: Resource-bounded episode workers

The runner SHALL execute up to the configured number of independent episodes in separate containers with CPU and memory limits and isolated ROS and Gazebo communication.

#### Scenario: Four workers share one host
- **WHEN** a campaign starts with a worker limit of four and more than four pending jobs
- **THEN** at most four jobs run concurrently and each uses a distinct ROS domain and Gazebo partition without host networking.

### Requirement: Durable attempts and recovery

The runner SHALL save each planned job and attempt outcome and retry only infrastructure failures under a bounded retry policy.

#### Scenario: A worker stops mid-episode
- **WHEN** a worker crashes or exceeds its wall-clock deadline
- **THEN** the runner terminates its container, preserves available diagnostics, and requeues the job if a retry remains.

#### Scenario: Resume a campaign
- **WHEN** the same saved plan is reopened after interruption
- **THEN** completed behavior results are reused and unfinished jobs remain eligible for execution.

### Requirement: Indexed campaign results

The project SHALL index run bundles by score, tier, scenario hash, and strategy and export a static HTML campaign report.

#### Scenario: Inspect an existing run
- **WHEN** a user filters a campaign by tier and strategy
- **THEN** matching attempts link to their bundles and the report includes aggregate outcomes and separate infrastructure counts.
