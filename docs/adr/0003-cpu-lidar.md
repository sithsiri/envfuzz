# ADR 0003: CPU LiDAR measurement and decision

## Status

Provisional pending Docker-capable hardware.

## Measurement

No Docker daemon is available in the development environment used to prepare
Phase 1 (`docker: command not found`), so scans/second, CPU usage, and achieved
real-time factor are **not measured**. This is an explicit limitation, not a
passing benchmark.

## Recommendation

Retain the standard Gazebo sensor topic contract and use Mesa/llvmpipe in the
headless image first. If the required 360-ray sensor is not viable, replace the
rendering implementation with a CPU ray-casting plugin without changing
`/scan`; this can materially affect performance but not scenario or oracle
contracts.

