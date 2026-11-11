"""Compare completed episode budgets and preserve navigation regression evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path

from .campaign_index import refresh_index
from .results import EpisodeResult
from .scenario import load_scenario
from .scoring import read_trace
from .workers import WorkerLimits, run_worker


def regression_signature(bundle: Path, layout: str) -> tuple | None:
    result = EpisodeResult.model_validate_json((bundle / "result.json").read_text(encoding="utf-8"))
    map_error = any(event.kind == "map_error" for event in result.events)
    if result.classification != "behavior" or (result.status == "completed" and not map_error):
        return None
    path = bundle / "telemetry.jsonl"
    truth, _ = read_trace(path) if path.is_file() else ([], [])
    terminal = (math.floor(truth[-1].x + 0.5), math.floor(truth[-1].y + 0.5)) if truth else None
    return layout, result.status, map_error, terminal


def analyze_campaign(root: Path, output: Path) -> dict:
    import sqlite3

    refresh_index(root)
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    by_strategy = {method: [] for method in plan["manifest"]["methods"]}
    infrastructure, interrupted = Counter(), Counter()
    with sqlite3.connect(root / "results.sqlite3") as database:
        for strategy, classification, status, bundle, layout, case in database.execute(
            "SELECT strategy, classification, status, bundle, layout, case_id FROM attempts "
            "ORDER BY case_id, attempt"
        ):
            if classification == "behavior":
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
                            "matched": regression_signature(bundle, case["signature"][0]) == expected})
        valid = sum(row["classification"] == "behavior" for row in results)
        matching = sum(row["matched"] for row in results)
        return {**case, "replays": results, "valid_replays": valid,
                "matching_replays": matching, "reproduction_rate": matching / valid if valid else None}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(replay_case, index, case) for index, case in enumerate(report["corpus"], 1)]
        report["corpus"] = [future.result() for future in futures]
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
