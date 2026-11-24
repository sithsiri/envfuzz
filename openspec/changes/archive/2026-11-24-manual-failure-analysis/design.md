# Design

## Context

See `proposal.md` for the motivation. EnvFuzz already has campaign indexes,
durable attempt provenance, replay summaries, per-episode result bundles,
trajectory reports, telemetry, logs, and runtime metadata. The design should
reuse these artifacts instead of inventing a second execution or evidence
system.

## Goals / Non-Goals

**Goals:**

- Create deterministic candidate and review exports from authoritative campaign
  attempts.
- Preserve links to immutable bundle paths and existing diagnostic artifacts.
- Separate human classification data from episode result data.
- Support optional video capture for live episodes and replay cases.
- Provide a static HTML/CSV workflow that works without a database service.
- Provide a local web-hosted review workflow that extends the static report
  with filtering, manual edits, replay status, and on-demand video generation.

**Non-Goals:**

- Replacing `result.json`, metrics, telemetry, or the durable claim ledger.
- Automatically inferring root causes or assigning confidence.
- Making video capture mandatory for evaluation or replay.
- Building a hosted collaboration or review database.
- Recording every campaign episode by default.

## Decisions

1. **Keep review state outside authoritative result bundles.**

   Campaign results remain authoritative execution evidence. Human annotations
   will be stored in a versioned `manual-review.json` aggregate and, optionally,
   in per-case `manual-review.json` files under the campaign or evidence
   archive. This prevents analyst edits from changing hashes or campaign outcomes.

2. **Export candidates from the existing campaign index.**

   The candidate exporter will select behavior episodes with `timeout` or
   `mission_failed` status from authoritative results. It will join existing
   case metadata and attempt metrics, and add existing replay data when present.
   Infrastructure outcomes remain in separate campaign counts.

3. **Use a small stable review schema.**

   Records will include:

   - campaign, case, attempt, bundle, and replay origin
   - observed status and existing infrastructure/behavior classification
   - review status
   - failure category
   - root-cause category
   - confidence
   - reproducibility
   - evidence summary
   - presentation-candidate flag
   - video path

   The JSON schema will be versioned and validated before writing. Video and
   evidence fields will be optional.

4. **Use a static report as the primary human surface.**

   The campaign report will be generated as HTML with CSV/JSON exports. It will
   filter candidates by outcome, category, confidence, review status, layout,
   and replay reproducibility. Bundle and diagnostic links will use existing
   relative paths rather than copying data.

   An interactive `envfuzz review` command will provide the preferred reviewer
   experience by serving a local loopback web application. Static campaign
   report generation remains valid and usable without a server.

   The GUI will use the existing candidate export, campaign SQLite index,
   bundle-relative artifacts, manual-review JSON, and replay CLI operations.
   It will not maintain a separate review database or duplicate authoritative
   attempt state.

5. **Make video capture a separate optional artifact.**

   Capture configuration will be passed only to episodes where recording is
   requested. The bundle writer will reference a relative video file and an
   explicit recording status in `timings.json` or a dedicated metadata file.
   A capture failure will not change the navigation `status`, `classification`,
   or score.

   Gazebo view capture will be preferred over synthetic frame assembly because
   it preserves the visible simulator state. Encoding and container format will
   be validated without changing the episode contract.

6. **Select replay and presentation cases explicitly.**

   The review queue may mark candidates for replay and presentation use, but
   replay execution remains an existing campaign or CLI operation. Only cases
   marked presentation candidates will be included in a presentation archive.

7. **Keep replay and video work asynchronous and bounded.**

   The GUI will submit replay requests to a server-side job runner and poll for
   status. Jobs will run with an existing replay or Docker execution backend,
   preserve an explicit replay origin, and write their own bundle under the
   campaign review area. Repeated replay requests will remain separate,
   inspectable operations rather than overwriting prior evidence.

   Video capture is opt-in per replay or later generated artifact. A capture
   failure is visible in job and bundle metadata and does not change the
   navigation result.

8. **Bind the local server safely by default.**

   The server will listen on loopback (`127.0.0.1`) by default and open a
   browser on the operator's machine. Non-loopback binding and remote origin
   policies require explicit CLI configuration. Replay and file access remain
   constrained to the selected campaign and its review output directories.

## Risks / Trade-offs

- Recording video can consume disk, CPU, or wall time → Capture is opt-in and
  bounded; recording metadata remains separate from simulation outcomes.
- Human categories can become inconsistent → Provide a documented taxonomy,
  confidence levels, and schema validation.
- Relative links can break after a campaign is moved → Export bundle-relative
  paths and verify target existence before generating the report.
- Replay can show the same case but miss the original signature → Store original
  and replay signatures independently and require manual confirmation.
- Large campaigns can create large HTML reports → Export CSV/JSON and paginate
  or filter static report rows.
- Replay/video jobs consume compute and disk → Use explicit submissions,
  bounded concurrency, visible status, and a persistent review output directory.
- A web server can expose unintended files or mutate evidence → Bind to loopback
  by default, restrict paths, and keep manual review state separate from
  authoritative bundles.

## Migration Plan

1. Add simulator-free candidate export and review-schema unit tests.
2. Generate CSV/JSON queue files for existing campaigns without modifying old
   bundles or campaign indexes.
3. Add the static HTML review report and human-use guide.
4. Add optional live/replay video capture behind an explicit CLI option.
5. Verify that normal runs remain byte-contract compatible and that capture
   failure is reported separately.

## Open Questions

None. The exact video encoder, while an implementation choice, can be selected
during implementation based on available Gazebo and host tooling.
