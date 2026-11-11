"""Compare a nightly campaign's performance with checked-in regression thresholds.

Each metric in the thresholds file may declare a ``minimum`` or ``maximum``
gate, an ``objective_minimum`` that is tracked but never fails the run, a
``reference`` measurement, and the ``source`` that justifies these values.
The report is written as JSON next to the campaign and as Markdown to
``$GITHUB_STEP_SUMMARY`` when that variable is set. A gate violation, or a
gated metric that could not be measured, exits with status 1. Invalid
thresholds, a thresholds file without the gates in ``GATED_METRICS``, and
non-finite or malformed measurements exit with status 2.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from statistics import median

from ci_json import is_count, is_number, load_json

SCHEMA_VERSION = 1
METRICS = (
    "median_end_to_end_real_time_factor",
    "median_mission_real_time_factor",
    "infrastructure_attempt_rate",
    "episodes_per_minute",
    "diagnostic_replay_agreement",
)
# Metrics whose gate must be declared in the thresholds file.
GATED_METRICS = ("median_end_to_end_real_time_factor",)
GATE_KEYS = frozenset({"minimum", "maximum"})
NUMERIC_KEYS = frozenset({"minimum", "maximum", "objective_minimum", "reference"})
ALLOWED_KEYS = NUMERIC_KEYS | {"source"}
ATTEMPT_RESULTS = ("case-*/attempt-*/result.json", "case-*/attempt-*/bundle/result.json")
SECONDS_PER_MINUTE = 60


def load_thresholds(path: Path) -> dict[str, dict]:
    document = load_json(path)
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: expected schema_version {SCHEMA_VERSION}")
    metrics = document.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError(f"{path}: expected a metrics object")
    for name, entry in metrics.items():
        _validate_entry(path, name, entry)
    ungated = [name for name in GATED_METRICS if not GATE_KEYS & metrics.get(name, {}).keys()]
    if ungated:
        raise ValueError(f"{path}: missing gates for {ungated}")
    return metrics


def _validate_entry(path: Path, name: str, entry: object) -> None:
    if name not in METRICS:
        raise ValueError(f"{path}: unknown metric {name}")
    if not isinstance(entry, dict) or not isinstance(entry.get("source"), str) or not entry["source"]:
        raise ValueError(f"{path}: {name} needs a source justifying its values")
    unknown = entry.keys() - ALLOWED_KEYS
    if unknown:
        raise ValueError(f"{path}: {name} has unknown keys {sorted(unknown)}")
    for key in NUMERIC_KEYS & entry.keys():
        if not is_number(entry[key]):
            raise ValueError(f"{path}: {name}.{key} must be a finite number")


def _attempt_results(root: Path) -> list[tuple[Path, dict]]:
    paths = sorted({path for pattern in ATTEMPT_RESULTS for path in root.glob(pattern)})
    results = []
    for path in paths:
        result = load_json(path)
        if not isinstance(result, dict) or not isinstance(result.get("classification"), str):
            raise ValueError(f"{path}: expected a result with a classification")
        results.append((path.parent, result))
    return results


def _optional_number(source: Path, document: object, key: str) -> float | None:
    value = document.get(key) if isinstance(document, dict) else None
    if value is not None and not is_number(value):
        raise ValueError(f"{source}: {key} must be a finite number")
    return value


def _mission_rate(bundle: Path) -> float | None:
    path = bundle / "metrics.json"
    if not path.is_file():
        return None
    return _optional_number(path, load_json(path), "mission_real_time_factor")


def _replay_agreement(root: Path) -> float | None:
    path = root / "analysis.json"
    if not path.is_file():
        return None
    analysis = load_json(path)
    corpus = analysis.get("corpus", []) if isinstance(analysis, dict) else None
    if not isinstance(corpus, list) or not all(isinstance(case, dict) for case in corpus):
        raise ValueError(f"{path}: expected a corpus list")
    counts = [(case.get("valid_replays", 0), case.get("matching_replays", 0)) for case in corpus]
    if not all(is_count(valid) and is_count(matching) and matching <= valid for valid, matching in counts):
        raise ValueError(f"{path}: replay counts must be integers with matching <= valid")
    valid = sum(valid for valid, _ in counts)
    matching = sum(matching for _, matching in counts)
    return matching / valid if valid else None


def measure_campaign(root: Path, wall_seconds: float | None) -> dict[str, float | None]:
    attempts = _attempt_results(root)
    behavior = [(bundle, result) for bundle, result in attempts if result["classification"] == "behavior"]
    infrastructure = sum(result["classification"] == "infrastructure" for _, result in attempts)
    counted = len(behavior) + infrastructure
    end_to_end = [rate for bundle, result in behavior
                  if (rate := _optional_number(bundle, result, "real_time_factor")) is not None]
    mission = [rate for bundle, _ in behavior if (rate := _mission_rate(bundle)) is not None]
    throughput = None
    if wall_seconds and behavior:
        throughput = len(behavior) / (wall_seconds / SECONDS_PER_MINUTE)
    return {
        "median_end_to_end_real_time_factor": median(end_to_end) if end_to_end else None,
        "median_mission_real_time_factor": median(mission) if mission else None,
        "infrastructure_attempt_rate": infrastructure / counted if counted else None,
        "episodes_per_minute": throughput,
        "diagnostic_replay_agreement": _replay_agreement(root),
    }


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def _evaluate_metric(name: str, value: float | None, entry: dict) -> tuple[dict, str | None]:
    row = {"value": value, **entry}
    gated = "minimum" in entry or "maximum" in entry
    failure = None
    if gated and value is None:
        failure = "not measured"
    elif "minimum" in entry and value < entry["minimum"]:
        failure = f"{_format(value)} is below minimum {_format(entry['minimum'])}"
    elif "maximum" in entry and value > entry["maximum"]:
        failure = f"{_format(value)} is above maximum {_format(entry['maximum'])}"
    if failure:
        row["status"] = "fail"
    elif gated:
        row["status"] = "pass"
    else:
        row["status"] = "tracked" if value is not None else "not measured"
    if "objective_minimum" in entry:
        row["objective_met"] = None if value is None else value >= entry["objective_minimum"]
    if "reference" in entry and value is not None:
        row["change_from_reference"] = value - entry["reference"]
    return row, None if failure is None else f"{name}: {failure}"


def evaluate(metrics: dict[str, float | None], thresholds: dict[str, dict]) -> dict:
    rows, failures = {}, []
    for name in METRICS:
        if name not in metrics and name not in thresholds:
            continue
        rows[name], failure = _evaluate_metric(name, metrics.get(name), thresholds.get(name, {}))
        if failure:
            failures.append(failure)
    return {"schema_version": SCHEMA_VERSION, "metrics": rows, "failures": failures}


def render_markdown(report: dict) -> str:
    verdict = "failed" if report["failures"] else "passed"
    lines = [
        f"## Benchmark regression check {verdict}", "",
        "| Metric | Value | Status | Minimum | Maximum | Objective | Reference |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for name, row in report["metrics"].items():
        cells = [_format(row.get(key)) for key in ("minimum", "maximum", "objective_minimum", "reference")]
        lines.append(f"| {name} | {_format(row['value'])} | {row['status']} | {' | '.join(cells)} |")
    lines.extend(f"- {failure}" for failure in (report["failures"] or ["No regressions."]))
    return "\n".join(["", *lines, ""]) + "\n"


def _wall_seconds(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number of seconds")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--campaign-wall-seconds", type=_wall_seconds,
                        help="Wall time of the campaign step, used for episodes per minute")
    parser.add_argument("--output", type=Path, help="Report path (default: CAMPAIGN/benchmark.json)")
    args = parser.parse_args()
    try:
        thresholds = load_thresholds(args.thresholds)
        metrics = measure_campaign(args.campaign, args.campaign_wall_seconds)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    report = {"campaign": str(args.campaign), **evaluate(metrics, thresholds)}
    output = args.output or args.campaign / "benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = render_markdown(report)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary, end="")
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
