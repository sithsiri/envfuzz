"""Strategy analysis selects the ledger's authoritative attempt for durable campaigns."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from envfuzz.analysis import analyze_campaign
from envfuzz.campaign import prepare_campaign
from envfuzz.claims import ClaimError, ClaimStore, JobClaim
from envfuzz.results import EpisodeResult

MANIFEST = "layouts: [corridor]\nmethods: [uniform]\nseeds: [3]\nepisodes_per_cell: 2\n"
LEGACY_KEYS = {"shared_budget", "shared_budget_by_layout", "strategies", "case_definition", "corpus"}


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def finish(store: ClaimStore, claim: JobClaim, worker: str, status: str, classification: str) -> None:
    bundle = store.root / f"case-{claim.case_id:06d}" / f"attempt-{claim.attempt:06d}" / "bundle"
    bundle.mkdir(parents=True)
    result = EpisodeResult(status=status, classification=classification)
    (bundle / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
    store.complete(claim, worker, result, bundle)


@pytest.fixture
def durable_campaign(tmp_path) -> Path:
    """Case 1: a lost worker finishes after a retry committed. Case 2: budget exhausted."""
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    root = tmp_path / "shared"
    _, cases = prepare_campaign(manifest, root)
    clock = FakeClock()
    store = ClaimStore(root, clock=clock)
    store.initialize(len(cases), retries=1)
    store.approve(["host-a", "host-b"])
    lost = store.claim("host-a", lease_s=10)
    clock.now = 20.0
    retry = store.claim("host-b", lease_s=10)
    finish(store, retry, "host-b", "completed", "behavior")
    finish(store, lost, "host-a", "mission_failed", "behavior")
    for worker in ("host-a", "host-b"):
        finish(store, store.claim(worker, lease_s=10), worker, "watchdog", "infrastructure")
    return root


def test_analysis_uses_the_authoritative_attempt_not_a_late_duplicate(durable_campaign):
    report = analyze_campaign(durable_campaign, durable_campaign / "analysis.json")

    uniform = report["strategies"]["uniform"]
    assert uniform["outcomes"] == {"completed": 1}
    assert uniform["completed_behavior_episodes"] == 1
    assert uniform["infrastructure_attempts"] == 2
    assert report["corpus"] == []
    assert report["durable_ledger"] == {
        "authoritative_cases": 1, "incomplete_cases": [2], "duplicate_behavior_attempts": 1,
        "uncommitted_bundles": 0,
    }


def test_analysis_reads_the_ledger_without_modifying_it(durable_campaign):
    before = (durable_campaign / "claims.sqlite3").read_bytes()

    analyze_campaign(durable_campaign, durable_campaign / "analysis.json")

    assert (durable_campaign / "claims.sqlite3").read_bytes() == before


def test_case_without_an_authoritative_result_is_not_replaced_by_another_attempt(tmp_path):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    root = tmp_path / "shared"
    _, cases = prepare_campaign(manifest, root)
    store = ClaimStore(root)
    store.initialize(len(cases), retries=0)
    store.approve(["host-a"])
    finish(store, store.claim("host-a", lease_s=10), "host-a", "completed", "behavior")
    unrecorded = root / "case-000002" / "attempt-000001" / "bundle"
    unrecorded.mkdir(parents=True)
    (unrecorded / "result.json").write_text(
        EpisodeResult(status="mission_failed", classification="behavior").model_dump_json(), encoding="utf-8")

    report = analyze_campaign(root, root / "analysis.json")

    assert report["strategies"]["uniform"]["outcomes"] == {"completed": 1}
    assert report["durable_ledger"]["incomplete_cases"] == [2]


def test_campaign_without_a_ledger_keeps_the_lowest_behavior_attempt(durable_campaign, tmp_path):
    plain = tmp_path / "plain"
    shutil.copytree(durable_campaign, plain)
    (plain / "claims.sqlite3").unlink()

    report = analyze_campaign(plain, plain / "analysis.json")

    assert set(report) == LEGACY_KEYS
    assert report["strategies"]["uniform"]["outcomes"] == {"mission_failed": 1}
    assert [entry["representative"] for entry in report["corpus"]] == ["case-000001/attempt-000001/bundle"]


def test_analysis_rejects_a_ledger_from_another_plan(durable_campaign, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(durable_campaign, other)
    with (other / "plan.json").open("a", encoding="utf-8") as handle:
        handle.write("\n")

    with pytest.raises(ClaimError, match="different campaign plan"):
        analyze_campaign(other, other / "analysis.json")


def test_analysis_rejects_an_authoritative_result_changed_after_commit(durable_campaign):
    changed = durable_campaign / "case-000001" / "attempt-000002" / "bundle" / "result.json"
    changed.write_text(EpisodeResult(status="mission_failed", classification="behavior", score=7)
                       .model_dump_json(), encoding="utf-8")

    with pytest.raises(ClaimError, match="changed after"):
        analyze_campaign(durable_campaign, durable_campaign / "analysis.json")


def test_analysis_counts_uncommitted_bundles_separately_from_duplicates(tmp_path):
    manifest = tmp_path / "campaign.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    root = tmp_path / "shared"
    _, cases = prepare_campaign(manifest, root)
    store = ClaimStore(root)
    store.initialize(len(cases), retries=1)
    finish(store, store.claim("host-a", lease_s=600), "host-a", "completed", "behavior")
    in_flight = store.claim("host-b", lease_s=600)
    bundle = root / "case-000002" / f"attempt-{in_flight.attempt:06d}" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "result.json").write_text(
        EpisodeResult(status="completed", classification="behavior").model_dump_json(), encoding="utf-8")

    report = analyze_campaign(root, root / "analysis.json")

    assert report["durable_ledger"] == {
        "authoritative_cases": 1, "incomplete_cases": [2], "duplicate_behavior_attempts": 0,
        "uncommitted_bundles": 1,
    }
