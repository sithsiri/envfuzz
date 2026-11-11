# Multi-host campaigns

`envfuzz campaign` on one host is the default way to run a campaign. Durable
workers let trusted hosts share one campaign directory instead. Each host
claims numbered jobs from a ledger in that directory. It runs every job through
the same resource-limited Docker worker as `campaign --workers`, and the
organizer reconciles one authoritative result per case.

Status: the claim ledger, lease recovery, retry budget, result deduplication,
and the worker circuit breaker are covered by fake-worker tests
(`tests/test_claims.py`, `tests/test_multihost_workers.py`, and
`tests/test_analysis_durable.py`). A live two-host CPU run has not been
recorded yet. The commands for it are in
[Two-host CPU smoke run](#two-host-cpu-smoke-run).

## How it works

- `campaign-approve` freezes `plan.json` and creates `claims.sqlite3` next to
  it. The ledger records its schema version (currently 3), the SHA-256 of
  `plan.json`, the case count, the retry budget, and the approved worker IDs.
  It will not create a new ledger in a directory that already has single-host
  results (`case-*` folders or `results.csv`). That check runs before anything
  is written, so a refused approval leaves the directory unchanged. Use a
  fresh directory instead.
- `campaign-approve` is the only command that creates the ledger. Two
  commands write `plan.json`, and only when it does not exist yet:
  `campaign-approve`, and `envfuzz campaign` (including `--plan-only`) in a
  directory without a ledger. Neither ever rewrites an existing plan.
- `campaign-worker` only joins an existing campaign. It compares its manifest
  with the frozen plan. It then reads the ledger version, plan hash, case count,
  retry budget, and its own worker ID. If any check fails, it exits with code 2
  without changing any file. That includes a worker started before
  `campaign-approve` and a worker that isn't approved.
- `claims.sqlite3` and `plan.json` must be regular files. Every command
  refuses a symlink in either place.
- Each claim is a new numbered attempt under a lease. Episodes go to
  `case-NNNNNN/attempt-NNNNNN/` in the same layout that `campaign --workers`
  uses (`inputs/`, `outputs/`, `worker.json`, `worker.log`, `bundle/`).
- An attempt row is written once when the attempt is claimed and once when it
  finishes. The ledger refuses to record the same attempt twice.
- The first behavior result committed for a case becomes its authoritative
  result, and that choice never changes. A later behavior result for the same
  case, such as one from a worker that lost its lease and then finished, is
  recorded as a `duplicate`. A behavior result is accepted only from the
  attempt's own bundle. That bundle's `result.json` must exist and match the
  reported status and classification.
- The Docker worker reports `launch_failed` when `docker run` exits with code
  125 (for example, the image is missing on that host) or when Docker cannot be
  executed. It still writes `bundle/result.json`, so the attempt is recorded
  with its bundle path and hashes. Some attempts produce no result of their
  own: the attempt folder already exists, the worker raises, or it returns
  without writing `result.json`. Those are recorded with no bundle path and
  no hashes (`launch_failed`, or `simulator_failed` for a missing
  `result.json`). A folder left over from an earlier run is never taken as
  their result.
- A finished attempt that is not a behavior result re-queues the case while
  that attempt still holds it. This covers infrastructure failures, launch
  failures, and interrupted episodes. If a newer attempt holds the case, a late
  failure is recorded but does not touch the newer attempt.

### What the ledger's SQLite triggers enforce

- `job_attempts`: a finished row cannot be updated. An unfinished row's case,
  attempt number, worker, host, claim time, and lease cannot change. Rows
  cannot be deleted. An existing `(case, attempt)` cannot be inserted again,
  so `INSERT OR REPLACE` is refused.
- `jobs`: rows cannot be deleted or inserted again. The case ID and plan hash
  cannot change, the attempt counter cannot decrease, and an authoritative
  attempt cannot change once set.
- `ledger_meta`: entries cannot be updated, deleted, or inserted again.

Each envfuzz connection also sets `PRAGMA recursive_triggers=ON`. Job state,
leases, and the retry budget used are ordinary columns that envfuzz itself
updates. Direct SQL can still change them. The triggers stop accidental edits
and tools that write through SQLite. They do not stop a host with write access
to the shared directory, which can drop the triggers or replace the file. That
case is out of scope: see [Approved workers](#approved-workers).

## Leases and retries

| Setting | Where | Meaning |
| --- | --- | --- |
| `--retries N` | `campaign-approve` | Each case may be charged `N + 1` attempts. The ledger records `N` once. |
| `--retries N` | `campaign-worker` | Optional. If given, it must match the ledger; otherwise the ledger value is used. |
| `--lease-s S` | `campaign-worker` | Wall-clock lease per claim. Default 600. |
| `--wait/--no-wait` | `campaign-worker` | With `--wait` (the default), the worker keeps polling while another worker holds a live lease, so it can take over a job whose owner disappears. |
| `--poll-s S` | `campaign-worker` | Polling interval while waiting. Default 10. |
| `--max-consecutive-failures K` | `campaign-worker` | Stop this worker after `K` infrastructure failures in a row. Default 3. It is not stored anywhere. |

Each claim charges the case's budget. The charge stays for an expired lease,
an infrastructure failure after the episode started (for example a watchdog
or simulator failure), and an interrupted episode. A `launch_failed` attempt
means the episode never started. If it still holds the case when it reports,
the charge is refunded, so a host that cannot launch episodes never exhausts a
case. This differs from single-host `campaign --workers`, where only
infrastructure failures count. A case that uses up its budget without a
behavior result is marked `exhausted`. That includes a case whose last lease
expired, so it never stays `claimed`. If a lost worker later delivers a
behavior result for an exhausted case, that result becomes authoritative.
`claims.json` shows each job's `attempt_count` and `budget_used`.

Because launch failures cost nothing, a broken host would otherwise retry the
same case forever. The worker counts infrastructure failures in a row, with
launch failures included, and resets the count on any other result. When the
count reaches `--max-consecutive-failures`, the worker stops, prints the last
failure, and exits with code 2. It also exits with code 2 if every attempt it
ran was an infrastructure failure. Other workers are unaffected.

A lease must outlast the longest time one attempt can hold its claim, or a
healthy worker would lose its job partway through. The worker refuses any
lease at or below `watchdog + 120` seconds:

- the container deadline grace (20 s);
- the Docker inspect (10 s) and remove (15 s) timeouts;
- two ledger lock waits of up to 30 s each;
- a 15 s margin.

For `examples/phase2-campaign.yaml` and the smoke manifest below (both have
watchdog 80), the lease must exceed 200 s. Leases are capped at 24 hours and
are not renewed while an episode runs. Claim and finish times are read after
the ledger's write lock is acquired, so waiting for the lock does not shorten
the lease.

Each worker host stamps leases with its own wall clock. Lease expiry is a
watchdog, not simulation logic. Keep hosts synchronized with NTP. Clock skew
between hosts shortens or lengthens a lease by the amount of the skew. A lease
that ends more than 24 hours after the reader's clock cannot be valid. Workers
treat it as expired, and `claims.json` marks that attempt with
`clock_skew_suspected: true`.

Pressing Ctrl-C during an episode records an `interrupted` attempt and stops
that worker. The case is re-queued if it has budget left and is marked
`exhausted` if not. If a worker process is killed outright, nothing is
recorded: its lease expires and another worker retries the case.

## Approved workers

`campaign-approve --worker-id ID` adds IDs to the ledger's allowlist. A worker
ID has 1–64 characters from letters, digits, `.`, `_`, and `-`, and starts with
a letter or digit. `campaign-worker` refuses to start when the allowlist is
empty or does not contain its ID. Give every worker process a distinct ID.

The allowlist stops misconfigured or unexpected workers. It is not a security
boundary, because any host that can write the shared directory can also edit
the ledger and the bundles. Mount the campaign directory only on
organizer-controlled hosts.

## Shared filesystem requirements

The ledger is a SQLite database on the shared filesystem. Because SQLite
documents that WAL mode does not work over a network filesystem, the ledger
uses the default rollback journal instead. That mode depends on POSIX advisory
(`fcntl`) byte-range locks. SQLite warns that many network filesystems
implement these locks incorrectly. If locking is broken, two workers can claim
the same attempt or corrupt the ledger.

- Use a shared POSIX filesystem whose locking works across hosts, for example
  NFS with working network locks. Do not mount it with `nolock`,
  `local_lock=posix`, or `local_lock=all`, which keep locks local to one host.
- Fake-worker tests prove only that claims are exclusive between threads and
  processes on a single host. Cross-host locking on your filesystem is untested
  until you run the two-host smoke run on it.
- If the filesystem's locking is in doubt, use the single-host fallback.
- `campaign --workers` coordinates through `flock` on `.campaign.lock`, not
  through the ledger. `envfuzz campaign`, including `--plan-only` and
  `--workers`, refuses a directory that already has `claims.sqlite3`. It exits
  with code 2 and changes no file, so the two modes do not mix.

Bundle paths in the ledger are relative to the campaign directory. Mount the
directory at the same path on every host to keep commands identical.

## Diagnostics and reconciliation

```sh
envfuzz campaign-reconcile /shared/pilot
```

This writes three files and exits with code 2 unless every case has an
authoritative result:

- `results.csv`: one row per case, using the same columns as single-host
  campaigns. The row comes from the authoritative attempt. If there isn't one,
  it comes from the latest finished attempt. Cases with no finished attempt
  are left out. A row for an attempt recorded without a bundle has an empty
  `bundle` and no metrics.
- `claims.json`: the full ledger view. It covers the plan hash, retry budget,
  approved workers, per-state counts, and each job's `attempt_count` and
  `budget_used`. For every attempt it records the worker ID, host name, claim
  time, lease expiry, finish time, status, classification, score, reason,
  bundle path, and the SHA-256 of `result.json` and `metrics.json`. Each
  attempt has an `outcome`: `authoritative`, `duplicate`, `failed`, `running`,
  or `lease_expired`. An unfinished attempt counts as `running` while its own
  lease is live, even if another attempt has already completed the case.
- `results.sqlite3`, rebuilt for `envfuzz report` and `envfuzz analyze`.

Reconciliation runs in this order:

1. It checks the ledger without writing: version, plan hash, case count, and
   retry budget. A refused reconciliation leaves the ledger byte-for-byte
   unchanged.
2. It checks every attempt it reports from, and every ledger-recorded attempt
   it indexes:
   - `result.json` and `metrics.json` still have the SHA-256 recorded at
     commit time (a missing `metrics.json` must also have been missing at
     commit);
   - the status and classification in `result.json` match the ledger row;
   - the bundle path stays inside the campaign.
3. It rebuilds `results.sqlite3`.
4. Only after all of that succeeds does it replace `results.csv` and
   `claims.json`, each through a temporary file and an atomic rename.

Other bundle files, such as `telemetry.jsonl` and the logs, are not hashed or
checked. Reconciliation can run while workers are active.

When `claims.sqlite3` exists, `envfuzz report` and `envfuzz analyze` index
only the attempts the ledger recorded as finished, reading each one from its
verified bundle. A stray `attempt-N/result.json` beside the bundle and
unrecorded attempt folders are ignored, so `results.sqlite3` agrees with
`results.csv`. `analyze` counts only each case's authoritative attempt. It
does not verify the telemetry it reads to build regression signatures. Cases without an authoritative result are left out
of the strategy budgets and listed under `durable_ledger.incomplete_cases`.
`durable_ledger` also reports:

- `duplicate_behavior_attempts`: committed, non-authoritative behavior results
  in the ledger;
- `uncommitted_bundles`: attempt folders that hold a result the ledger has not
  recorded, for example a worker that is still finishing.

Analysis opens the ledger read-only. The exception is a ledger left with a hot
rollback journal by a killed writer: one read-write open rolls that
transaction back first, as any worker would on its next access. Without a
ledger, `report` and `analyze` index every attempt folder as before, and
`analyze` keeps the lowest-numbered behavior attempt per case.

Ledger and index failures are reported as a one-line error by
`campaign-approve`, `campaign-worker`, `campaign-reconcile`, `report`, and
`analyze`, each exiting with code 2. Examples: a lock held past the 30-second
busy timeout, a damaged file, broken filesystem locking, or an unusable
`results.sqlite3` (derived data that can be deleted and rebuilt). A ledger
without version metadata, such as one written by an earlier development build
in WAL mode, is refused as an unsupported, unversioned schema and left
unchanged.

## Single-host fallback

Either option keeps the same plan, bundle format, and worker limits:

- Run the whole campaign on one host with `envfuzz campaign MANIFEST --output
  DIR [--workers N]` in a directory without `claims.sqlite3`. This is the
  default path.
- Finish a durable campaign on one host by running a single `campaign-worker`
  against its directory, then `campaign-reconcile`. If the campaign moves to
  local disk, copy the whole directory while no worker is running.

## Two-host CPU smoke run

You need two Linux hosts, A and B. Each has Docker, the same envfuzz checkout
installed with `python -m pip install -e '.[test]'`, and the image built with
`docker build -t envfuzz:jazzy .`. Both mount the shared filesystem at
`/shared`. On each host, run `envfuzz doctor` and `docker image inspect
envfuzz:jazzy --format '{{.Id}}'` and record the output.

1. Organizer, on host A: write a small manifest and approve both workers.

   ```sh
   mkdir -p /shared
   cat > /shared/smoke.yaml <<'EOF'
   layouts: [corridor]
   methods: [uniform]
   seeds: [42]
   episodes_per_cell: 4
   timeout: 25
   watchdog: 80
   EOF
   envfuzz campaign-approve /shared/smoke.yaml --output /shared/smoke \
     --worker-id host-a --worker-id host-b --retries 1
   ```

2. Start both workers at the same time. Run host A's in the foreground:

   ```sh
   # host A
   envfuzz campaign-worker /shared/smoke.yaml --output /shared/smoke \
     --worker-id host-a --worker-cpus 2 --worker-memory 1536m --lease-s 240
   # host B
   envfuzz campaign-worker /shared/smoke.yaml --output /shared/smoke \
     --worker-id host-b --worker-cpus 2 --worker-memory 1536m --lease-s 240
   ```

3. Recovery check. While host A is running an episode, kill its worker
   process, then remove the orphaned container. `kill -9` does not stop a
   container that is already running.

   ```sh
   # host A
   pkill -9 -f 'envfuzz campaign-worker'
   docker ps --filter label=org.envfuzz.worker --format '{{.Names}}' | xargs -r docker rm -f
   ```

   Host B keeps polling. Host A's lease expires 240 s after its claim. After
   that, host B claims the abandoned case as a new attempt as soon as it
   finishes its current episode or its next poll runs. Then it exits once
   every case is finished.

4. Optional: after step 3, restart host A's worker with the same command. It
   picks up any case that is still queued or whose lease has expired.

5. Organizer: reconcile and inspect.

   ```sh
   envfuzz campaign-reconcile /shared/smoke
   python -m json.tool /shared/smoke/claims.json | less
   ```

Acceptance:

- `campaign-reconcile` exits 0 and reports `4 of 4 cases have an
  authoritative result`.
- In `claims.json`, every job is `completed` and has exactly one
  `authoritative` attempt.
- The case killed in step 3 shows host A's attempt as `lease_expired` and a
  later attempt by host B as `authoritative`.
- Attempts from both `host-a` and `host-b` appear, with their host names.
- Each authoritative bundle has the single-host layout and its own
  `worker.json` limits.

Record the hosts, filesystem and mount options, image IDs, and the outcome
counts from `claims.json`. Don't mark the run as passed until these are
recorded.
