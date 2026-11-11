# Verification and release workflows

## Status on GitHub

"Verify EnvFuzz" has run on push three times on `main`, on 2026-11-11 at
02:01Z, 02:02Z and 05:20Z. Each run failed at the headless evidence upload
with EACCES (for example runs 42943185190 and 42965113084). The root
integration container had written a 0700 pytest basetemp into `runs/ci`.
There have been no scheduled runs and no releases, so the nightly steps and
the release workflow have never run. Those runs used the workflow that was
committed at the time. The changes described below, including the ownership
fix, have not yet run on GitHub.

## Every push and pull request

The `unit` job installs the `test` and `lint` extras and runs `make lint`,
which byte-compiles the sources and runs `ruff check` on `src`, `tests` and
`docker`. It also runs `actionlint` on the workflows with `shellcheck`, both
pinned in the `lint` extra. The target fails if `shellcheck` is missing,
because otherwise actionlint would silently skip its shell checks. It then runs the unit and property suite under coverage. Coverage
is gated by `fail_under = 85` in `pyproject.toml`, the P3-09 default for
`src/`. Run `make coverage` to apply the same gate locally. The ruff rule
selection is explicit in `pyproject.toml`. Line length, import order,
pyupgrade and bugbear rules are deferred until `src/` is cleaned up.

The `headless` job builds the CPU image and runs `pytest -m integration`
inside it. pytest collects every `tests/*.py` file inside the image, so each
third-party module that a test imports at module level must be pinned in the
Dockerfile's pip line. `tests/test_ci_workflows.py` enforces this.
The integration basetemp stays in the container's `/tmp`. Afterwards it is
copied to `runs/ci/integration-pytest` and made readable. Before anything
reads or uploads `runs/ci`, an `if: always()` step changes its ownership to
the runner user, because worker and integration containers write it as
root.

Every job uploads its evidence even when a step fails: junit XML, test and
coverage logs for `unit`, and build, integration, campaign and check reports
for `headless`. Steps run under `bash -eo pipefail`, so output piped through
`tee` cannot hide a failing command.

## Nightly regression and performance

The nightly steps run on the 03:23 UTC schedule. To run them on demand,
dispatch the workflow with the `nightly` input set to true. The nightly run
has three parts.

1. The diagnostic pilot: the saved-seed sampling campaign, followed by
   `envfuzz analyze --replays 3`. Its regressions are freshly generated each
   night and are never gated.
2. Frozen fixture replays. `docker/replay_fixtures.py` copies each fixture
   in `benchmarks/replay-corpus.json` from `benchmarks/replay-fixtures/<id>/`.
   It checks that the archived bundle still produces the frozen expected
   signature, then replays the fixture `nightly_replays` times through the
   same `replay_corpus` machinery as `envfuzz analyze`.
   `docker/check_replay.py` fails a fixture in any of these cases:
   - its admission evidence has fewer than 20 valid replays, or agreement
     below its floor (0.95 to 1);
   - tonight produced fewer than `minimum_valid_replays` valid replays,
     including a night where every attempt was an infrastructure failure;
   - tonight's agreement is below the floor.

   Agreement is recomputed from replay rows and integer counts. Stored rates
   are ignored. Infrastructure attempts are reported separately.
   [`benchmarks/replay-fixtures/README.md`](../benchmarks/replay-fixtures/README.md)
   describes the fixture format.
3. `docker/check_benchmark.py` compares the pilot with
   `benchmarks/nightly-thresholds.json` and fails only on a gate violation.

**The required replay corpus is empty, so the replay-tolerance check
currently enforces nothing.** No fixture meets the
[admission rule](phase-3-verification.md#fixture-admission): the Phase 3
rerun reproduced 0 of 17 valid replays. When the corpus is empty, the check
passes and prints a warning annotation and a job-summary note saying that it
enforces nothing.

The benchmark check measures five values:

- median end-to-end real-time factor
- median mission real-time factor
- infrastructure-attempt rate
- episodes per minute over the campaign step's wall time
- diagnostic replay agreement

It writes `benchmark.json` and a job-summary table. Invalid or non-finite
thresholds or measurements, or a thresholds file that lacks the required
gate, fail with exit status 2. **The only failing gate is the 0.1 floor on
the median end-to-end real-time factor.** It is a coarse alarm carried over
from the earlier CI flag, not derived from a measurement. The reference
measurement is a 0.420× median from the Phase 3 pilot, taken on Docker
Desktop on ARM64, not on a GitHub-hosted runner. The other values are
reported, not gated: the infrastructure-attempt rate, the 5× mission-speed
objective and the 95% diagnostic-agreement objective. Diagnostic agreement
covers tonight's fresh pilot regressions, a different population from the
frozen fixtures, so it has no reference value. Add a gate only after a
measured baseline justifies it, and cite that baseline in the thresholds
file.

Performance is not yet tracked over time. Each nightly report exists only in
that run's `headless-evidence` artifact until the artifact retention period
expires, and nothing compares one run with another.

## Releases

A published GitHub release triggers native amd64 and arm64 jobs. Each job
builds its image, records `envfuzz doctor` output, and runs a CPU smoke
episode with 2 CPUs and 3 GB of memory. `docker/release_evidence.py manifest`
then writes `envfuzz-<tag>-<arch>-manifest.json`. The manifest records the
tag, the source revision, the image reference, the smoke result, the host
machine type with its native or emulated status, the resource limits, and
the component versions.

The job fails before pushing if any of these hold:

- the smoke episode did not complete;
- the image reports no ROS distribution;
- the tag minus a leading `v` is not exactly the packaged envfuzz version.

Tags must use the normalized PEP 440 spelling that `envfuzz doctor` reports,
for example `v0.2.0rc1`, not `v0.2.0-rc.1`. Bump the package version before
tagging.

An architecture job pushes only an immutable `candidate-<sha>-<arch>` tag.
The `publish` job needs both architecture jobs. It creates the
`<tag>-amd64` and `<tag>-arm64` tags and the multi-architecture `<tag>` index
from the two candidates, so no version tag exists unless both smoke runs
passed. If one architecture fails, the other architecture's candidate tag
remains in GHCR and no version tags are created. Fix the cause, then re-run
the failed jobs from the workflow run page. `publish` runs once both jobs
succeed. Delete stale candidate tags in GHCR by hand.

`publish` then attaches both manifests and the image index to the release
and rewrites the release notes. An author-written body is kept. An empty
body is replaced with notes from GitHub's
[generate release notes API](https://docs.github.com/en/rest/releases/releases#generate-release-notes-content-for-a-release).
Either way, the job appends a verification section that lists each
architecture's smoke result and component versions. A rerun replaces that
section rather than adding a second one. Note generation refuses
duplicate-architecture or mixed-release manifests. Images include
`/usr/share/envfuzz/versions.json` and OCI version, revision and source
labels.

The workflows follow the documented
[GitHub container publishing workflow](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
and use [native ARM64 runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
A release requires a versioned GitHub release, package-write access, and
contents-write access for the release assets and notes.

## Known follow-ups

- GHCR visibility after the first publish is unverified. A new package may be
  private and need to be made public in the package settings.
- `docker buildx imagetools create` retagging of single-platform candidates
  has not been run against GHCR.
- `actions/upload-artifact@v4` and `actions/download-artifact@v4` run on the
  deprecated Node 20 runtime. They are kept as they are for now and should be
  moved to a current major version.
