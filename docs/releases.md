# Verification and release workflows

The verification workflow runs the host lint and unit suite and builds a
CPU headless image for integration tests on pushes and pull requests.
Each run uploads coverage and headless evidence, including failed test
logs. A nightly run adds the saved-seed sampling pilot and replay diagnostics.

`docker/check_replay.py` writes a machine-readable verification report.
The nightly end-to-end real-time-factor floor is 0.1. Treat this as a coarse
alarm and review timing alongside host and image metadata. Required replay
cases can be declared with `--required FILE.json`, mapping representative
bundle paths to agreement floors from 0.95 to 1. Each required case needs at
least 20 valid replays and must meet its declared agreement floor. Diagnostic
cases remain visible even when they fail to reproduce.

A published GitHub release triggers native amd64 and arm64 builds. Each image
gets a CPU smoke episode before its architecture tag is pushed to GHCR.
The multi-architecture version tag is assembled only after both native
jobs pass. Each architecture exports its component manifest and smoke bundle
as release evidence. Images include `/usr/share/envfuzz/versions.json`
and OCI version, revision and source labels. GitHub's generated release
notes provide the change list.

The workflows follow the documented
[GitHub container publishing workflow](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
and use [native ARM64 runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
A release requires a versioned GitHub release and package-write access in the
repository. The checked-in workflows become active when they are pushed.
