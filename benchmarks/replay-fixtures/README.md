# Frozen replay fixtures

Each admitted fixture lives in `<id>/` and is listed in
`../replay-corpus.json` with its agreement `floor` (0.95 to 1),
`nightly_replays` (1 to 20) and `minimum_valid_replays`. No fixture has been
admitted yet. The Phase 3 rerun reproduced 0 of 17 valid replays
(`docs/phase-3-verification.md`), so this directory holds no fixtures.

A fixture directory contains:

- `bundle/`: the archived Gazebo run bundle. It must include
  `scenario.json`, `run.json`, `result.json` and `telemetry.jsonl`, plus the
  `nav2.yaml`, `baseline.json`, `oracle.yaml` and `profile.yaml` it ran with.
  `envfuzz analyze --replays` replays it the same way.
- `fixture.json`: `{"schema_version": 1, "id": "<id>", "expected_signature": [...]}`.
  The signature is the corpus signature from `analysis.json`.
  `docker/replay_fixtures.py` refuses to replay a fixture whose archived
  bundle no longer produces this signature.
- `admission.json`: the one-off admission evidence, with integer
  `valid_replays` (at least 20), `matching_replays` and
  `infrastructure_attempts`, plus the image, host and resource limits used.
  The admission replays must use the same worker limits as the nightly run.
  `docker/check_replay.py` recomputes agreement from the integer counts and
  ignores any stored rate.
