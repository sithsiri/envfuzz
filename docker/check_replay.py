"""Enforce replay agreement for frozen, admitted regression fixtures.

The corpus file lists fixture ids under ``benchmarks/replay-fixtures/<id>/``
with an agreement ``floor`` in [0.95, 1], the number of ``nightly_replays``
and the ``minimum_valid_replays`` a night needs. A fixture fails when its
admission evidence is missing or invalid, when tonight produced too few valid
replays, or when tonight's agreement is below its floor. Agreement is always
recomputed from replay rows and integer counts; stored rates are ignored.
An empty corpus passes and says loudly that nothing is enforced.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from ci_json import is_count, is_number, load_json

SCHEMA_VERSION = 1
# docs/phase-3-verification.md, "Fixture admission".
MINIMUM_ADMISSION_REPLAYS = 20
MINIMUM_AGREEMENT_FLOOR = 0.95
# Upper bound of `envfuzz analyze --replays`.
MAXIMUM_NIGHTLY_REPLAYS = 20
FIXTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
FIXTURE_FILES = ("fixture.json", "admission.json", "bundle/scenario.json", "bundle/run.json")
CLASSIFICATIONS = frozenset({"behavior", "infrastructure", "interrupted"})
EMPTY_WARNING = ("Required replay corpus is empty, so this check enforces nothing. "
                 "No fixture has been admitted (docs/phase-3-verification.md).")


def _validate_entry(path: Path, name: str, entry: object) -> None:
    if not FIXTURE_ID.match(name):
        raise ValueError(f"{path}: invalid fixture id {name!r}")
    if not isinstance(entry, dict) or set(entry) != {"floor", "nightly_replays", "minimum_valid_replays"}:
        raise ValueError(f"{path}: {name} needs exactly floor, nightly_replays and minimum_valid_replays")
    floor, nightly, minimum = entry["floor"], entry["nightly_replays"], entry["minimum_valid_replays"]
    if not is_number(floor) or not MINIMUM_AGREEMENT_FLOOR <= floor <= 1:
        raise ValueError(f"{path}: {name} floor must be in [{MINIMUM_AGREEMENT_FLOOR}, 1]")
    if not is_count(nightly) or not 1 <= nightly <= MAXIMUM_NIGHTLY_REPLAYS:
        raise ValueError(f"{path}: {name} nightly_replays must be 1 to {MAXIMUM_NIGHTLY_REPLAYS}")
    if not is_count(minimum) or not 1 <= minimum <= nightly:
        raise ValueError(f"{path}: {name} minimum_valid_replays must be 1 to nightly_replays")


def load_corpus(path: Path) -> dict:
    corpus = load_json(path)
    if not isinstance(corpus, dict) or corpus.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: expected schema_version {SCHEMA_VERSION}")
    if set(corpus) != {"schema_version", "fixtures"} or not isinstance(corpus["fixtures"], dict):
        raise ValueError(f"{path}: expected a fixtures object")
    for name, entry in corpus["fixtures"].items():
        _validate_entry(path, name, entry)
    return corpus


def parse_results(results: object, source: str) -> dict[str, list[dict]]:
    """Return replay rows by fixture id from replay_fixtures.py output."""
    fixtures = results.get("fixtures") if isinstance(results, dict) else None
    if not isinstance(fixtures, list):
        raise ValueError(f"{source}: expected a fixtures list")
    rows_by_id = {}
    for fixture in fixtures:
        name = fixture.get("id") if isinstance(fixture, dict) else None
        rows = fixture.get("replays") if isinstance(fixture, dict) else None
        if not isinstance(name, str) or name in rows_by_id or not isinstance(rows, list):
            raise ValueError(f"{source}: each fixture needs a unique id and a replays list")
        for row in rows:
            if (not isinstance(row, dict) or row.get("classification") not in CLASSIFICATIONS
                    or not isinstance(row.get("matched"), bool)):
                raise ValueError(f"{source}: {name} has a malformed replay row")
        rows_by_id[name] = rows
    return rows_by_id


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def _agreement(matching: int, valid: int) -> float | None:
    return matching / valid if valid else None


def _check_admission(directory: Path, floor: float) -> tuple[dict, str | None]:
    admission = load_json(directory / "admission.json")
    valid = admission.get("valid_replays") if isinstance(admission, dict) else None
    matching = admission.get("matching_replays") if isinstance(admission, dict) else None
    if not is_count(valid) or not is_count(matching) or matching > valid:
        return {}, "admission evidence is malformed"
    agreement = _agreement(matching, valid)
    summary = {"valid_replays": valid, "matching_replays": matching, "agreement": agreement}
    if valid < MINIMUM_ADMISSION_REPLAYS:
        return summary, f"admission has {valid} valid replays, at least {MINIMUM_ADMISSION_REPLAYS} required"
    if agreement < floor:
        return summary, f"admission {matching}/{valid} agreement {_format(agreement)} is below floor {floor}"
    return summary, None


def _check_tonight(rows: list[dict], entry: dict) -> tuple[dict, str | None]:
    valid = sum(row["classification"] == "behavior" for row in rows)
    matching = sum(row["classification"] == "behavior" and row["matched"] for row in rows)
    infrastructure = sum(row["classification"] == "infrastructure" for row in rows)
    agreement = _agreement(matching, valid)
    summary = {"attempts": len(rows), "valid_replays": valid, "matching_replays": matching,
               "infrastructure_attempts": infrastructure, "agreement": agreement}
    if valid < entry["minimum_valid_replays"]:
        return summary, (f"{valid} valid replays tonight, at least {entry['minimum_valid_replays']} "
                         f"required ({infrastructure} infrastructure attempts)")
    if agreement < entry["floor"]:
        return summary, (f"tonight {matching}/{valid} agreement {_format(agreement)} "
                         f"is below floor {entry['floor']}")
    return summary, None


def _check_fixture(directory: Path, entry: dict, rows: list[dict] | None) -> tuple[dict, list[str]]:
    missing = next((name for name in FIXTURE_FILES if not (directory / name).is_file()), None)
    if missing:
        return {"floor": entry["floor"]}, [f"frozen fixture is missing {missing}"]
    fixture = load_json(directory / "fixture.json")
    if not isinstance(fixture, dict) or fixture.get("id") != directory.name:
        return {"floor": entry["floor"]}, ["fixture.json does not name this fixture"]
    admission, admission_failure = _check_admission(directory, entry["floor"])
    report = {"floor": entry["floor"], "admission": admission}
    failures = [admission_failure] if admission_failure else []
    if rows is None:
        return report, [*failures, "no replays recorded tonight"]
    report["tonight"], tonight_failure = _check_tonight(rows, entry)
    return report, [*failures, *([tonight_failure] if tonight_failure else [])]


def check_fixtures(corpus: dict, fixtures: Path, results: object | None) -> dict:
    rows_by_id = {} if results is None else parse_results(results, "replay results")
    reports, failures = {}, []
    for name, entry in corpus["fixtures"].items():
        reports[name], problems = _check_fixture(fixtures / name, entry, rows_by_id.get(name))
        failures += [f"{name}: {problem}" for problem in problems]
    return {"schema_version": SCHEMA_VERSION, "enforced": bool(corpus["fixtures"]),
            "fixtures": reports, "failures": failures}


def _summary(report: dict) -> str:
    if not report["enforced"]:
        return f"## Required replay tolerance\n\n**{EMPTY_WARNING}**\n"
    verdict = "failed" if report["failures"] else "passed"
    lines = [f"## Required replay tolerance {verdict}", ""]
    lines += [f"- {failure}" for failure in report["failures"]] or ["- Every admitted fixture agreed."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--results", type=Path, help="replay_fixtures.py output for tonight")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        corpus = load_corpus(args.corpus)
        has_results = args.results is not None and args.results.is_file()
        report = check_fixtures(corpus, args.fixtures, load_json(args.results) if has_results else None)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["enforced"]:
        print(f"::warning::{EMPTY_WARNING}")
    print(json.dumps(report, indent=2))
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(_summary(report))
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
