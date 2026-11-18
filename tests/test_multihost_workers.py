"""Multi-host campaign workers exercised with fake episode workers (no Docker)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
from threading import Event, Lock, Thread

import pytest
from typer.testing import CliRunner

from envfuzz import workers
from envfuzz.analysis import analyze_campaign
from envfuzz.campaign import (
    LEASE_SAFETY_MARGIN_S, WorkerHalted, join_campaign, load_plan, minimum_lease, prepare_campaign,
    reconcile_durable_campaign, run_campaign, run_durable_worker, run_parallel_campaign,
)
from envfuzz.claims import BUSY_TIMEOUT_S, ClaimError, ClaimStore
from envfuzz.cli import app
from envfuzz.results import EpisodeResult
from envfuzz.scenario import Scenario
from envfuzz.workers import WorkerLimits, run_worker, worker_deadline

MANIFEST = (
    "layouts: [corridor]\nmethods: [uniform]\nseeds: [3]\nepisodes_per_cell: 3\n"
    "timeout: 5\nwatchdog: 10\n"
)
LIMITS = WorkerLimits(image="envfuzz:test", cpus=1.5, memory="900m")
LEASE_S = 200.0


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeWorker:
    """Imitates run_worker: creates a fresh attempt folder and writes the bundle result."""

    def __init__(self, outcome=None) -> None:
        self.outcome = outcome or (lambda case, attempt: ("completed", "behavior"))
        self.calls: list[dict] = []
        self.lock = Lock()

    def __call__(self, scenario, bundle: Path, timeout, watchdog, limits, **options) -> EpisodeResult:
        case = int(bundle.parent.parent.name.removeprefix("case-"))
        attempt = int(bundle.parent.name.removeprefix("attempt-"))
        with self.lock:
            self.calls.append({"case": case, "attempt": attempt, "bundle": bundle, "timeout": timeout,
                               "watchdog": watchdog, "limits": limits, "scenario": scenario})
        outcome = self.outcome(case, attempt)
        if isinstance(outcome, BaseException):
            raise outcome
        status, classification = outcome
        bundle.parent.mkdir(parents=True, exist_ok=False)
        bundle.mkdir()
        result = EpisodeResult(status=status, classification=classification, score=case)
        (bundle / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
        (bundle / "metrics.json").write_text(json.dumps({"path_length_m": float(case)}), encoding="utf-8")
        return result


@pytest.fixture
def campaign(tmp_path):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "shared"
    config, cases = prepare_campaign(manifest, output)
    store = ClaimStore(output)
    store.initialize(len(cases), retries=1)
    store.approve(["host-a", "host-b"])
    return manifest, config, cases, output


def run(config, cases, output, worker_id, *, clock=None, host=None, **options):
    store = ClaimStore(output, clock=clock, host=host or worker_id)
    return run_durable_worker(config, cases, output, worker_id, LIMITS, lease_s=LEASE_S,
                              store=store, **options)


def read_rows(output: Path) -> list[dict]:
    with (output / "results.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_two_hosts_split_the_campaign_and_reconcile_one_result_per_case(campaign, monkeypatch):
    _, config, cases, output = campaign
    fake = FakeWorker()
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)
    committed = {}
    threads = [Thread(target=lambda worker=worker: committed.update({worker: run(config, cases, output, worker)}))
               for worker in ("host-a", "host-b")]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(committed.values()) == 3
    assert sorted((call["case"], call["attempt"]) for call in fake.calls) == [(1, 1), (2, 1), (3, 1)]
    call = fake.calls[0]
    assert (call["timeout"], call["watchdog"], call["limits"]) == (5, 10, LIMITS)
    assert call["bundle"] == output.resolve() / f"case-{call['case']:06d}" / "attempt-000001" / "bundle"
    assert call["scenario"] == cases[call["case"] - 1].scenario
    complete, report = reconcile_durable_campaign(output)
    assert complete
    assert report["counts"]["completed"] == 3
    assert json.loads((output / "claims.json").read_text(encoding="utf-8")) == report
    rows = read_rows(output)
    assert [(row["case"], row["classification"], row["bundle"]) for row in rows] == [
        (str(case), "behavior", f"case-{case:06d}/attempt-000001/bundle") for case in (1, 2, 3)
    ]
    assert rows[0]["path_length_m"] == "1.0"
    assert (output / "results.sqlite3").is_file()


def test_lost_worker_is_recovered_and_its_late_result_is_deduplicated(campaign, monkeypatch):
    _, config, cases, output = campaign
    clock = FakeClock(0.0)
    fake = FakeWorker()
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)
    lost_store = ClaimStore(output, clock=clock, host="node-a")
    lost = lost_store.claim("host-a", lease_s=LEASE_S)

    clock.now = LEASE_S + 1
    assert run(config, cases, output, "host-b", clock=clock) == 3
    late = output / "case-000001" / "attempt-000001" / "bundle"
    late.mkdir(parents=True)
    result = EpisodeResult(status="mission_failed", classification="behavior")
    (late / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    assert not lost_store.complete(lost, "host-a", result, late)

    complete, report = reconcile_durable_campaign(output)
    assert complete
    first = report["jobs"][0]
    assert first["authoritative_attempt"] == 2
    assert [(row["worker_id"], row["host"], row["outcome"]) for row in first["attempts"]] == [
        ("host-a", "node-a", "duplicate"), ("host-b", "host-b", "authoritative"),
    ]
    assert read_rows(output)[0]["bundle"] == "case-000001/attempt-000002/bundle"
    assert read_rows(output)[0]["status"] == "completed"


def test_waiting_worker_recovers_a_job_after_the_live_lease_expires(campaign, monkeypatch):
    _, config, cases, output = campaign
    clock = FakeClock(0.0)
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())
    ClaimStore(output, clock=clock).claim("host-a", lease_s=LEASE_S)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    assert run(config, cases, output, "host-b", clock=clock, poll_s=50, sleep=sleep) == 3
    assert sleeps == [50, 50, 50, 50]
    report = ClaimStore(output, clock=clock).diagnostics()
    assert [row["outcome"] for row in report["jobs"][0]["attempts"]] == ["lease_expired", "authoritative"]


def test_worker_without_wait_leaves_live_claims_to_their_owner(campaign, monkeypatch):
    _, config, cases, output = campaign
    clock = FakeClock(0.0)
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())
    ClaimStore(output, clock=clock).claim("host-a", lease_s=LEASE_S)

    assert run(config, cases, output, "host-b", clock=clock, wait=False) == 2

    report = ClaimStore(output, clock=clock).diagnostics()
    assert report["jobs"][0]["state"] == "claimed"
    complete, _ = reconcile_durable_campaign(output)
    assert not complete
    assert [row["case"] for row in read_rows(output)] == ["2", "3"]


def test_infrastructure_failures_exhaust_the_budget_and_reconcile_reports_them(campaign, monkeypatch):
    _, config, cases, output = campaign
    fake = FakeWorker(lambda case, attempt: ("watchdog", "infrastructure") if case == 2
                      else ("completed", "behavior"))
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)

    assert run(config, cases, output, "host-a") == 2

    assert [(call["case"], call["attempt"]) for call in fake.calls] == [(1, 1), (2, 1), (2, 2), (3, 1)]
    complete, report = reconcile_durable_campaign(output)
    assert not complete
    assert report["counts"] == {"queued": 0, "claimed": 0, "completed": 2, "exhausted": 1}
    row = read_rows(output)[1]
    assert (row["classification"], row["bundle"]) == ("infrastructure", "case-000002/attempt-000002/bundle")


def test_worker_exceptions_are_recorded_as_launch_failures(campaign, monkeypatch):
    _, config, cases, output = campaign
    fake = FakeWorker(lambda case, attempt: OSError("docker missing") if (case, attempt) == (1, 1)
                      else ("completed", "behavior"))
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)

    assert run(config, cases, output, "host-a") == 3

    first = ClaimStore(output).diagnostics()["jobs"][0]["attempts"][0]
    assert (first["status"], first["classification"], first["reason"], first["result_sha256"]) == (
        "launch_failed", "infrastructure", "docker missing", None,
    )


def test_reconcile_reports_launch_failures_that_left_no_bundle(campaign, monkeypatch):
    _, config, cases, output = campaign
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker(
        lambda case, attempt: OSError("docker missing") if case == 3 else ("completed", "behavior")))

    with pytest.raises(WorkerHalted) as halted:
        run(config, cases, output, "host-a")
    assert halted.value.committed == 2

    complete, report = reconcile_durable_campaign(output)
    assert not complete and report["counts"] == {"queued": 1, "claimed": 0, "completed": 2, "exhausted": 0}
    row = read_rows(output)[2]
    assert (row["status"], row["classification"], row["bundle"]) == ("launch_failed", "infrastructure", "")
    assert row["case"] == "3" and report["jobs"][2]["attempt_count"] == 3


def test_worker_rejects_bad_polling_and_a_foreign_store(campaign, tmp_path):
    _, config, cases, output = campaign

    with pytest.raises(ValueError, match="poll"):
        run_durable_worker(config, cases, output, "host-a", LIMITS, lease_s=LEASE_S, poll_s=0)
    with pytest.raises(ValueError, match="claim store"):
        run_durable_worker(config, cases, output, "host-a", LIMITS, lease_s=LEASE_S,
                           store=ClaimStore(tmp_path))


def test_interrupted_episode_stops_the_worker_and_requeues_the_job(campaign, monkeypatch):
    _, config, cases, output = campaign
    fake = FakeWorker(lambda case, attempt: ("interrupted", "interrupted"))
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)

    assert run(config, cases, output, "host-a") == 0

    assert len(fake.calls) == 1
    report = ClaimStore(output).diagnostics()
    assert report["jobs"][0]["state"] == "queued"
    assert report["jobs"][0]["attempts"][0]["outcome"] == "failed"


def test_unapproved_worker_is_rejected_before_any_episode(campaign, monkeypatch):
    _, config, cases, output = campaign
    fake = FakeWorker()
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)

    with pytest.raises(ClaimError, match="not approved"):
        run(config, cases, output, "intruder")
    assert fake.calls == []


def test_worker_retry_budget_must_match_the_ledger(campaign, monkeypatch):
    _, config, cases, output = campaign
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())

    with pytest.raises(ClaimError, match="retry budget"):
        run(config, cases, output, "host-a", retries=4)
    assert run(config, cases, output, "host-a", retries=1) == 3


def test_lease_must_outlast_the_container_deadline_and_lock_waits(campaign):
    _, config, cases, output = campaign

    assert worker_deadline(10) > 10 + workers.CONTAINER_GRACE_S
    assert minimum_lease(10) == worker_deadline(10) + 2 * BUSY_TIMEOUT_S + LEASE_SAFETY_MARGIN_S
    with pytest.raises(ValueError, match="lease"):
        run_durable_worker(config, cases, output, "host-a", LIMITS, lease_s=minimum_lease(10))


def test_single_host_schedulers_refuse_a_durable_campaign_directory(campaign):
    _, config, cases, output = campaign

    with pytest.raises(ValueError, match="campaign-worker"):
        run_campaign(config, cases, output)
    with pytest.raises(ValueError, match="campaign-worker"):
        run_parallel_campaign(config, cases, output, 1, LIMITS)


def test_reconcile_rejects_a_modified_authoritative_result(campaign, monkeypatch):
    _, config, cases, output = campaign
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())
    run(config, cases, output, "host-a")
    tampered = output / "case-000002" / "attempt-000001" / "bundle" / "result.json"
    tampered.write_text(EpisodeResult(status="completed", classification="behavior", score=99)
                        .model_dump_json(), encoding="utf-8")

    with pytest.raises(ClaimError, match="changed after"):
        reconcile_durable_campaign(output)


def test_reconcile_rejects_a_ledger_for_another_plan(campaign, tmp_path):
    _, _, _, output = campaign
    other = tmp_path / "other"
    other.mkdir()
    (other / "plan.json").write_bytes((output / "plan.json").read_bytes())
    (other / "claims.sqlite3").write_bytes((output / "claims.sqlite3").read_bytes())
    plan = json.loads((other / "plan.json").read_text(encoding="utf-8"))
    plan["cases"] = plan["cases"][:2]
    (other / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(ClaimError, match="different campaign plan"):
        reconcile_durable_campaign(other)


def test_load_plan_requires_a_frozen_plan(tmp_path, campaign):
    _, config, cases, output = campaign

    assert load_plan(output) == (config, cases)
    with pytest.raises(ValueError, match="plan.json"):
        load_plan(tmp_path / "missing")


def test_cli_approve_worker_and_reconcile_round_trip(tmp_path, monkeypatch):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "shared"
    fake = FakeWorker()
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)
    runner = CliRunner()

    approved = runner.invoke(app, ["campaign-approve", str(manifest), "--output", str(output),
                                   "--worker-id", "host-a", "--worker-id", "host-b", "--retries", "1"])
    assert approved.exit_code == 0, approved.output
    assert "host-a, host-b" in approved.output
    rejected = runner.invoke(app, ["campaign-worker", str(manifest), "--output", str(output),
                                   "--worker-id", "intruder"])
    assert rejected.exit_code == 2
    assert "not approved" in rejected.output
    worker = runner.invoke(app, ["campaign-worker", str(manifest), "--output", str(output),
                                 "--worker-id", "host-a", "--lease-s", "200", "--no-wait"])
    assert worker.exit_code == 0, worker.output
    assert "3 authoritative results committed" in worker.output
    reconciled = runner.invoke(app, ["campaign-reconcile", str(output)])
    assert reconciled.exit_code == 0, reconciled.output
    assert "3 of 3 cases have an authoritative result" in reconciled.output
    assert (output / "claims.json").is_file()


def test_cli_reconcile_reports_incomplete_campaigns_and_bad_input(tmp_path, campaign):
    _, _, _, output = campaign
    runner = CliRunner()

    incomplete = runner.invoke(app, ["campaign-reconcile", str(output)])
    assert incomplete.exit_code == 2
    assert "0 of 3 cases" in incomplete.output
    empty = tmp_path / "empty"
    empty.mkdir()
    missing = runner.invoke(app, ["campaign-reconcile", str(empty)])
    assert missing.exit_code == 2
    approve = runner.invoke(app, ["campaign-approve", str(tmp_path / "campaign.yaml"), "--output",
                                  str(output), "--worker-id", "../bad"])
    assert approve.exit_code == 2


# Parallel single-host scheduler and the Docker worker, with Docker replaced by fakes.

def test_parallel_campaign_resumes_interrupted_attempts_and_retries_infrastructure(tmp_path, monkeypatch):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "runs"
    config, cases = prepare_campaign(manifest, output)
    stale = output / "case-000001" / "attempt-000001" / "outputs" / "bundle"
    stale.mkdir(parents=True)
    removed = []
    monkeypatch.setattr("envfuzz.campaign.remove_worker", lambda bundle: removed.append(bundle))
    fake = FakeWorker(lambda case, attempt: ("watchdog", "infrastructure") if (case, attempt) == (2, 1)
                      else ("completed", "behavior"))
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)

    assert run_parallel_campaign(config, cases, output, 2, LIMITS, retries=1)

    assert sorted((call["case"], call["attempt"]) for call in fake.calls) == [(1, 2), (2, 1), (2, 2), (3, 1)]
    interrupted = output / "case-000001" / "attempt-000001" / "bundle" / "result.json"
    assert EpisodeResult.model_validate_json(interrupted.read_text()).classification == "interrupted"
    assert removed
    assert [row["bundle"] for row in read_rows(output)] == [
        "case-000001/attempt-000002/bundle", "case-000002/attempt-000002/bundle",
        "case-000003/attempt-000001/bundle",
    ]
    assert run_parallel_campaign(config, cases, output, 2, LIMITS, retries=1)
    assert len(fake.calls) == 4
    with pytest.raises(ValueError, match="Worker count"):
        run_parallel_campaign(config, cases, output, 0, LIMITS)


def test_parallel_campaign_cleans_up_after_keyboard_interrupt(tmp_path, monkeypatch):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "runs"
    config, cases = prepare_campaign(manifest, output)
    removed = []
    monkeypatch.setattr("envfuzz.campaign.remove_worker", lambda bundle: removed.append(bundle))
    started = Event()

    def worker(scenario, bundle, timeout, watchdog, limits, **options):
        bundle.mkdir(parents=True)
        started.set()
        raise KeyboardInterrupt

    monkeypatch.setattr("envfuzz.campaign.run_worker", worker)

    with pytest.raises(KeyboardInterrupt):
        run_parallel_campaign(config, cases, output, 1, LIMITS)
    assert started.is_set() and removed


def test_parallel_campaign_rejects_a_second_scheduler(tmp_path, monkeypatch):
    import fcntl

    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "runs"
    config, cases = prepare_campaign(manifest, output)
    with (output / ".campaign.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="already running"):
            run_parallel_campaign(config, cases, output, 1, LIMITS)


def _docker(monkeypatch, behaviour):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=command[-1] + "\n")
        if command[:2] == ["docker", "rm"]:
            return subprocess.CompletedProcess(command, 0)
        return behaviour(command, **kwargs)

    monkeypatch.setattr("envfuzz.workers.subprocess.run", fake_run)
    return commands


def test_run_worker_delivers_the_container_bundle_with_limits(tmp_path, monkeypatch):
    def episode(command, **kwargs):
        results = Path(command[command.index("--mount") + 3].split(",")[1].removeprefix("src="))
        delivered = results / "bundle"
        delivered.mkdir()
        (delivered / "result.json").write_text(
            EpisodeResult(status="completed", classification="behavior").model_dump_json())
        return subprocess.CompletedProcess(command, 0)

    commands = _docker(monkeypatch, episode)
    params = tmp_path / "nav2.yaml"
    params.write_text("controller: {}\n")
    bundle = tmp_path / "case" / "attempt-000001" / "bundle"

    result = run_worker(Scenario(), bundle, 5, 10, LIMITS, params=params, domain_id=12)

    assert result.classification == "behavior"
    assert (bundle / "result.json").is_file()
    command = commands[0]
    assert command[command.index("--network") + 1] == "none"
    assert (command[command.index("--cpus") + 1], command[command.index("--memory") + 1]) == ("1.5", "900m")
    assert "ENVFUZZ_WORKER_DOMAIN=12" in command and "/inputs/nav2.yaml" in command
    assert ["docker", "rm", "-f", workers.container_name(bundle)] in commands
    assert json.loads((bundle.parent / "worker.json").read_text())["ros_domain_id"] == 12


@pytest.mark.parametrize(("raised", "status", "classification"), [
    (subprocess.TimeoutExpired("docker", 30), "watchdog", "infrastructure"),
    (KeyboardInterrupt(), "interrupted", "interrupted"),
    (OSError("no docker"), "launch_failed", "infrastructure"),
])
def test_run_worker_classifies_container_failures(tmp_path, monkeypatch, raised, status, classification):
    def episode(command, **kwargs):
        assert kwargs["timeout"] == 10 + workers.CONTAINER_GRACE_S
        raise raised

    _docker(monkeypatch, episode)
    bundle = tmp_path / "attempt-000001" / "bundle"

    result = run_worker(Scenario(), bundle, 5, 10, LIMITS)

    assert (result.status, result.classification) == (status, classification)
    assert EpisodeResult.model_validate_json((bundle / "result.json").read_text()) == result


def test_run_worker_rejects_an_invalid_container_result(tmp_path, monkeypatch):
    def episode(command, **kwargs):
        results = Path(command[command.index("--mount") + 3].split(",")[1].removeprefix("src="))
        (results / "bundle").mkdir()
        (results / "bundle" / "result.json").write_text('{"status": "bogus"}')
        return subprocess.CompletedProcess(command, 0)

    _docker(monkeypatch, episode)

    result = run_worker(Scenario(), tmp_path / "attempt-000001" / "bundle", 5, 10, LIMITS)

    assert result.status == "simulator_failed"
    assert result.reason.startswith("Worker returned an invalid result")


def test_remove_worker_tolerates_missing_docker(tmp_path, monkeypatch):
    def missing(command, **kwargs):
        raise OSError("docker not installed")

    monkeypatch.setattr("envfuzz.workers.subprocess.run", missing)

    workers.remove_worker(tmp_path / "bundle")


# Review findings: stale folders, join-only workers, verified results, clean ledger errors.

def snapshot(directory: Path) -> dict[str, bytes]:
    return {path.relative_to(directory).as_posix(): path.read_bytes() if path.is_file() else b"<dir>"
            for path in sorted(directory.rglob("*"))}


def test_durable_worker_never_records_a_stale_attempt_folder_as_its_result(campaign, monkeypatch):
    _, config, cases, output = campaign
    for attempt in (1, 2):
        stale = output / "case-000001" / f"attempt-{attempt:06d}" / "bundle"
        stale.mkdir(parents=True)
        (stale / "result.json").write_text(
            EpisodeResult(status="completed", classification="behavior", score=99).model_dump_json())
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())

    assert run(config, cases, output, "host-a") == 3

    complete, report = reconcile_durable_campaign(output)
    assert complete
    attempts = report["jobs"][0]["attempts"]
    assert [(row["status"], row["bundle"], row["result_sha256"] is None) for row in attempts] == [
        ("launch_failed", None, True), ("launch_failed", None, True),
        ("completed", "case-000001/attempt-000003/bundle", False)]
    assert report["jobs"][0]["budget_used"] == 1
    assert read_rows(output)[0]["bundle"] == "case-000001/attempt-000003/bundle"


def test_campaign_approve_refuses_a_directory_with_single_host_results(tmp_path):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "runs"
    prepare_campaign(manifest, output)
    (output / "case-000001" / "attempt-000001" / "bundle").mkdir(parents=True)

    result = CliRunner().invoke(app, ["campaign-approve", str(manifest), "--output", str(output),
                                      "--worker-id", "host-a"])

    assert result.exit_code == 2
    assert "single-host" in result.output
    assert not (output / "claims.sqlite3").exists()


def test_completion_rejects_a_result_file_that_disagrees_with_the_reported_result(campaign):
    _, _, _, output = campaign
    store = ClaimStore(output)
    claim = store.claim("host-a", lease_s=LEASE_S)
    bundle = output / "case-000001" / "attempt-000001" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "result.json").write_text(
        EpisodeResult(status="completed", classification="behavior").model_dump_json())

    with pytest.raises(ClaimError, match="disagrees"):
        store.complete(claim, "host-a", EpisodeResult(status="watchdog", classification="infrastructure"), bundle)


def test_reconcile_rejects_metrics_changed_after_commit(campaign, monkeypatch):
    _, config, cases, output = campaign
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())
    run(config, cases, output, "host-a")
    (output / "case-000003" / "attempt-000001" / "bundle" / "metrics.json").write_text('{"path_length_m": 0}')

    with pytest.raises(ClaimError, match="metrics.json changed"):
        reconcile_durable_campaign(output)


def test_worker_before_approval_leaves_the_directory_untouched(tmp_path, monkeypatch):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "shared"
    output.mkdir()
    fake = FakeWorker()
    monkeypatch.setattr("envfuzz.campaign.run_worker", fake)
    runner = CliRunner()
    command = ["campaign-worker", str(manifest), "--output", str(output), "--worker-id", "early",
               "--retries", "5", "--lease-s", "200"]

    empty = runner.invoke(app, command)
    assert empty.exit_code == 2 and "campaign-approve" in empty.output
    assert snapshot(output) == {}

    prepare_campaign(manifest, output)
    planned = snapshot(output)
    no_ledger = runner.invoke(app, command)
    assert no_ledger.exit_code == 2 and "campaign-approve" in no_ledger.output
    assert snapshot(output) == planned

    approved = runner.invoke(app, ["campaign-approve", str(manifest), "--output", str(output),
                                   "--worker-id", "host-a", "--retries", "1"])
    assert approved.exit_code == 0, approved.output
    ledger = snapshot(output)
    stray = runner.invoke(app, command[:-4] + ["--lease-s", "200"])
    assert stray.exit_code == 2 and "not approved" in stray.output
    assert snapshot(output) == ledger
    assert fake.calls == []


def test_worker_manifest_must_match_the_frozen_plan(campaign, tmp_path):
    _, config, cases, output = campaign
    other = tmp_path / "other.yaml"
    other.write_text(MANIFEST.replace("episodes_per_cell: 3", "episodes_per_cell: 4"), encoding="utf-8")

    assert join_campaign(tmp_path / "campaign.yaml", output) == (config, cases)
    with pytest.raises(ValueError, match="different campaign manifest"):
        join_campaign(other, output)


def test_cli_reports_a_locked_ledger_without_a_traceback(campaign, monkeypatch):
    import sqlite3

    _, _, _, output = campaign
    monkeypatch.setattr("envfuzz.claims.BUSY_TIMEOUT_S", 0.05)
    holder = sqlite3.connect(output / "claims.sqlite3", isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        result = CliRunner().invoke(app, ["campaign-reconcile", str(output)])
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "locked" in result.output


def test_campaign_plan_only_refuses_a_durable_campaign_directory(campaign):
    manifest, _, _, output = campaign
    before = snapshot(output)

    result = CliRunner().invoke(app, ["campaign", str(manifest), "--output", str(output), "--plan-only"])

    assert result.exit_code == 2
    assert "campaign-worker" in result.output
    assert snapshot(output) == before


# Adversarial review (da-ledger): broken hosts, verified index, atomic reconcile, refused commands.

def broken(case, attempt):
    return ("launch_failed", "infrastructure")


def test_misconfigured_host_exhausts_no_cases_and_halts(campaign, monkeypatch):
    _, config, cases, output = campaign
    failing = FakeWorker(broken)
    monkeypatch.setattr("envfuzz.campaign.run_worker", failing)

    with pytest.raises(WorkerHalted, match="3 consecutive infrastructure failures") as halted:
        run(config, cases, output, "host-b")

    assert halted.value.committed == 0
    assert len(failing.calls) == 3
    assert ClaimStore(output).diagnostics()["counts"] == {"queued": 3, "claimed": 0, "completed": 0, "exhausted": 0}
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())
    assert run(config, cases, output, "host-a") == 3
    assert reconcile_durable_campaign(output)[0]


def test_lone_broken_worker_terminates_even_while_waiting(campaign, monkeypatch):
    _, config, cases, output = campaign
    failing = FakeWorker(lambda case, attempt: OSError("docker: command not found"))
    monkeypatch.setattr("envfuzz.campaign.run_worker", failing)

    with pytest.raises(WorkerHalted, match="docker: command not found"):
        run(config, cases, output, "host-b", wait=True, max_consecutive_failures=5)

    assert len(failing.calls) == 5


def test_worker_that_records_only_failures_exits_nonzero(campaign, monkeypatch):
    _, config, cases, output = campaign
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker(lambda case, attempt: ("watchdog", "infrastructure")))

    with pytest.raises(WorkerHalted, match="only infrastructure failures") as halted:
        run(config, cases, output, "host-a", max_consecutive_failures=10)

    assert halted.value.committed == 0
    assert ClaimStore(output).diagnostics()["counts"]["exhausted"] == 3


def test_consecutive_failure_limit_must_be_positive(campaign):
    _, config, cases, output = campaign

    with pytest.raises(ValueError, match="consecutive"):
        run(config, cases, output, "host-a", max_consecutive_failures=0)


def test_missing_result_file_is_recorded_as_an_infrastructure_failure(campaign, monkeypatch):
    _, config, cases, output = campaign
    healthy = FakeWorker()

    def without_result(scenario, bundle, *args, **kwargs):
        result = healthy(scenario, bundle, *args, **kwargs)
        if bundle.parent.name == "attempt-000001" and bundle.parent.parent.name == "case-000001":
            (bundle / "result.json").unlink()
        return result

    monkeypatch.setattr("envfuzz.campaign.run_worker", without_result)

    assert run(config, cases, output, "host-a") == 3

    first = ClaimStore(output).diagnostics()["jobs"][0]["attempts"][0]
    assert (first["status"], first["bundle"], first["outcome"]) == ("simulator_failed", None, "failed")


def test_cli_worker_reports_a_halt_without_a_traceback(tmp_path, monkeypatch):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "shared"
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker(broken))
    runner = CliRunner()
    runner.invoke(app, ["campaign-approve", str(manifest), "--output", str(output), "--worker-id", "host-b"])

    result = runner.invoke(app, ["campaign-worker", str(manifest), "--output", str(output), "--worker-id",
                                 "host-b", "--lease-s", "200", "--max-consecutive-failures", "2"])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "2 consecutive infrastructure failures" in result.output


def test_durable_index_and_analysis_read_only_ledger_verified_bundles(campaign, monkeypatch):
    import sqlite3

    _, config, cases, output = campaign
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())
    run(config, cases, output, "host-a")
    forged = EpisodeResult(status="mission_failed", classification="behavior", score=999, tiers_hit=["T1"])
    (output / "case-000001" / "attempt-000001" / "result.json").write_text(forged.model_dump_json())
    unrecorded = output / "case-000001" / "attempt-000009" / "bundle"
    unrecorded.mkdir(parents=True)
    (unrecorded / "result.json").write_text(forged.model_dump_json())

    assert reconcile_durable_campaign(output)[0]
    report = analyze_campaign(output, output / "analysis.json")

    with sqlite3.connect(output / "results.sqlite3") as database:
        indexed = database.execute("SELECT case_id, attempt, status, bundle FROM attempts ORDER BY case_id").fetchall()
        tiers = database.execute("SELECT COUNT(*) FROM tiers").fetchone()[0]
    assert indexed == [(row["case"] and int(row["case"]), 1, row["status"], row["bundle"]) for row in read_rows(output)]
    assert tiers == 0
    assert report["strategies"]["uniform"]["outcomes"] == {"completed": 3}
    assert report["corpus"] == []
    assert report["durable_ledger"]["uncommitted_bundles"] == 1


def test_reconcile_with_a_broken_index_fails_cleanly_and_writes_nothing(campaign, monkeypatch):
    _, config, cases, output = campaign
    monkeypatch.setattr("envfuzz.campaign.run_worker", FakeWorker())
    run(config, cases, output, "host-a")
    (output / "results.sqlite3").write_bytes(b"garbage" * 200)
    runner = CliRunner()

    with pytest.raises(ValueError, match="results.sqlite3"):
        reconcile_durable_campaign(output)
    for command in (["campaign-reconcile", str(output)], ["analyze", str(output)], ["report", str(output)]):
        result = runner.invoke(app, command)
        assert result.exit_code == 2, command
        assert isinstance(result.exception, SystemExit)
        assert "results.sqlite3" in " ".join(result.output.split())
    assert not (output / "results.csv").exists()
    assert not (output / "claims.json").exists()


def test_refused_reconcile_leaves_the_ledger_unchanged(campaign):
    _, _, _, output = campaign
    stale = ClaimStore(output, clock=FakeClock(0.0))
    stale.claim("host-a", lease_s=10)
    stale.claim("host-b", lease_s=10)
    with (output / "plan.json").open("a", encoding="utf-8") as handle:
        handle.write(" ")
    before = (output / "claims.sqlite3").read_bytes()

    with pytest.raises(ClaimError, match="different campaign plan"):
        reconcile_durable_campaign(output)

    assert (output / "claims.sqlite3").read_bytes() == before


def test_refused_approve_leaves_the_directory_unchanged(tmp_path):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    output = tmp_path / "single"
    (output / "case-000001" / "attempt-000001").mkdir(parents=True)
    before = snapshot(output)

    result = CliRunner().invoke(app, ["campaign-approve", str(manifest), "--output", str(output),
                                      "--worker-id", "host-a"])

    assert result.exit_code == 2 and "single-host" in result.output
    assert snapshot(output) == before
