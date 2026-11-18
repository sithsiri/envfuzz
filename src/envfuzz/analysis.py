"""Compare completed episode budgets and preserve navigation regression evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import math
from pathlib import Path
import re
import sqlite3

from .campaign_index import INDEX_TIMEOUT_S, index_error, refresh_index
from .claims import LEDGER_FILENAME, ClaimStore
from .results import EpisodeResult
from .scenario import load_scenario
from .scoring import read_trace
from .workers import WorkerLimits, run_worker

ATTEMPT_FOLDER = re.compile(r"case-(\d+)/attempt-(\d+)")


def regression_signature(bundle: Path, layout: str) -> tuple | None:
    result = EpisodeResult.model_validate_json((bundle / "result.json").read_text(encoding="utf-8"))
    map_error = any(event.kind == "map_error" for event in result.events)
    if result.classification != "behavior" or (result.status == "completed" and not map_error):
        return None
    path = bundle / "telemetry.jsonl"
    truth, _ = read_trace(path) if path.is_file() else ([], [])
    terminal = (math.floor(truth[-1].x + 0.5), math.floor(truth[-1].y + 0.5)) if truth else None
    return layout, result.status, map_error, terminal


def _durable_view(root: Path) -> dict:
    """Summarize the claim ledger: authoritative attempts, duplicates, and uncommitted bundles."""
    entries = ClaimStore(root).finished_attempts()
    finished = {(case, record["attempt"]) for case, entry in entries.items() for record in entry["finished"]}
    uncommitted = 0
    for attempt in root.glob("case-*/attempt-*"):
        match = ATTEMPT_FOLDER.fullmatch(f"{attempt.parent.name}/{attempt.name}")
        if match is None:
            continue
        has_result = (attempt / "bundle" / "result.json").is_file() or (attempt / "result.json").is_file()
        uncommitted += has_result and (int(match[1]), int(match[2])) not in finished
    return {
        "authoritative": {case: entry["authoritative_attempt"] for case, entry in entries.items()},
        "duplicates": sum(record["classification"] == "behavior" and record["attempt"] != entry["authoritative_attempt"]
                          for entry in entries.values() for record in entry["finished"]),
        "uncommitted": uncommitted,
    }


def _index_rows(root: Path) -> list[tuple]:
    try:
        with closing(sqlite3.connect(root / "results.sqlite3", timeout=INDEX_TIMEOUT_S)) as database:
            return database.execute(
                "SELECT strategy, classification, status, bundle, layout, case_id, attempt FROM attempts "
                "ORDER BY case_id, attempt"
            ).fetchall()
    except sqlite3.Error as error:
        raise index_error(root, error) from error


def analyze_campaign(root: Path, output: Path) -> dict:
    # A durable campaign counts only the attempt its claim ledger made authoritative; its index
    # holds only ledger-recorded attempts read from their hash-verified bundles.
    durable = _durable_view(root) if (root / LEDGER_FILENAME).exists() else None
    authoritative = durable["authoritative"] if durable is not None else None
    refresh_index(root)
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    by_strategy = {method: [] for method in plan["manifest"]["methods"]}
    infrastructure, interrupted = Counter(), Counter()
    for strategy, classification, status, bundle, layout, case, attempt in _index_rows(root):
        if classification == "behavior":
            if authoritative is not None and authoritative.get(case) != attempt:
                continue
            if not by_strategy[strategy] or by_strategy[strategy][-1][-1] != case:
                by_strategy[strategy].append((status, bundle, layout, case))
        elif classification == "infrastructure":
            infrastructure[strategy] += 1
        else:
            interrupted[strategy] += 1
    layout_counts = [Counter(row[2] for row in rows) for rows in by_strategy.values()]
    shared = {layout: min(counts[layout] for counts in layout_counts)
              for layout in plan["manifest"]["layouts"]}
    budget = sum(shared.values())
    summaries = {}
    corpus = defaultdict(list)
    for strategy, rows in by_strategy.items():
        seen, coverage, curve = set(), set(), []
        counts = Counter()
        selected = []
        for status, bundle, layout, case in rows:
            if counts[layout] >= shared[layout]:
                continue
            counts[layout] += 1
            selected.append(status)
            signature = regression_signature(root / bundle, layout)
            if signature is not None:
                corpus[signature].append({"strategy": strategy, "bundle": bundle})
                seen.add(signature)
            challenge = plan["cases"][case - 1]["scenario"]["challenge"]
            coverage.add((layout, challenge.get("width_bin"), challenge.get("clutter_count"),
                          challenge.get("goal_distance_bin")))
            curve.append(len(seen))
        summaries[strategy] = {
            "completed_behavior_episodes": len(rows), "infrastructure_attempts": infrastructure[strategy],
            "interrupted_attempts": interrupted[strategy], "outcomes": dict(Counter(selected)),
            "covered_categories": len(coverage), "distinct_cases_by_episode": curve,
        }
    report = {
        "shared_budget": budget, "shared_budget_by_layout": shared, "strategies": summaries,
        "case_definition": "layout, mission status, map-error flag, terminal position rounded to one-meter grid",
        "corpus": [{"signature": signature, "representative": matches[0]["bundle"], "runs": matches}
                   for signature, matches in corpus.items()],
    }
    if durable is not None:
        selected_cases = {row[-1] for rows in by_strategy.values() for row in rows}
        report["durable_ledger"] = {
            "authoritative_cases": len(selected_cases),
            "incomplete_cases": sorted(set(range(1, len(plan["cases"]) + 1)) - selected_cases),
            "duplicate_behavior_attempts": durable["duplicates"],
            "uncommitted_bundles": durable["uncommitted"],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def replay_corpus(root: Path, output: Path, report: dict, repeats: int, workers: int, image: str) -> None:
    replay_root = root / "corpus-replays"

    def replay_case(index: int, case: dict) -> dict:
        original = root / case["representative"]
        scenario = load_scenario(original / "scenario.json")
        config = json.loads((original / "run.json").read_text(encoding="utf-8"))
        expected = regression_signature(original, case["signature"][0])
        files = {key: original / name for key, name in (
            ("baseline", "baseline.json"), ("params", "nav2.yaml"),
            ("oracle", "oracle.yaml"), ("profile", "profile.yaml"),
        ) if (original / name).is_file()}
        results = []
        for number in range(1, repeats + 1):
            trial = replay_root / f"case-{index:04d}" / f"repeat-{number:03d}"
            bundle = trial / "bundle"
            result_file = bundle / "result.json"
            if result_file.is_file():
                result = EpisodeResult.model_validate_json(result_file.read_text(encoding="utf-8"))
            else:
                retry = 1
                while trial.exists():
                    trial = trial.with_name(f"repeat-{number:03d}-retry-{retry}")
                    retry += 1
                bundle = trial / "bundle"
                result = run_worker(scenario, bundle, config["timeout"], config["watchdog"], WorkerLimits(image),
                                    **files)
            results.append({"bundle": bundle.relative_to(root).as_posix(), "status": result.status,
                            "classification": result.classification,
                            "real_time_factor": result.real_time_factor,
                            "matched": result.classification == "behavior" and expected is not None
                            and regression_signature(bundle, case["signature"][0]) == expected})
        valid = sum(row["classification"] == "behavior" for row in results)
        matching = sum(row["matched"] for row in results)
        return {**case, "replays": results, "valid_replays": valid,
                "matching_replays": matching, "reproduction_rate": matching / valid if valid else None}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(replay_case, index, case) for index, case in enumerate(report["corpus"], 1)]
        report["corpus"] = [future.result() for future in futures]
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
