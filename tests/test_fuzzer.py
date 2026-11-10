from envfuzz.fuzzer import candidates, fuzz
from envfuzz.results import EpisodeResult


def test_seeded_candidates_repeat():
    left = [next(candidates(7)).canonical_json() for _ in range(1)]
    right = [next(candidates(7)).canonical_json() for _ in range(1)]
    assert left == right


def test_budget_counts_executions(tmp_path):
    results, rejected = fuzz(3, 5, lambda scenario: EpisodeResult(status="completed",
        classification="behavior"), tmp_path / "results.jsonl")
    assert len(results) == 3
    assert (tmp_path / "results.jsonl").read_text().count("\n") == 3
    assert rejected >= 0
