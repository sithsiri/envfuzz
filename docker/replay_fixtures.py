"""Replay frozen regression fixtures for the nightly replay-tolerance gate.

Each fixture in the corpus is copied from ``benchmarks/replay-fixtures/<id>/
bundle`` into a scratch root and replayed ``nightly_replays`` times through
``envfuzz.analysis.replay_corpus``, the same machinery as ``envfuzz analyze
--replays``. Before replaying, the archived bundle's regression signature
must still equal the frozen ``expected_signature``. The output lists every
replay row per fixture; ``check_replay.py`` evaluates it.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Callable

from ci_json import load_json
from check_replay import SCHEMA_VERSION, load_corpus

ReplayFn = Callable[[Path, Path, dict, int, int, str], None]
SignatureFn = Callable[[Path, str], "tuple | None"]


def _normalize(signature: object) -> object:
    return json.loads(json.dumps(signature))


def _stage(fixtures: Path, root: Path, name: str, signature: SignatureFn) -> dict:
    source = fixtures / name
    if not (source / "fixture.json").is_file() or not (source / "bundle").is_dir():
        raise ValueError(f"Frozen fixture {name} is missing fixture.json or bundle/")
    fixture = load_json(source / "fixture.json")
    expected = fixture.get("expected_signature") if isinstance(fixture, dict) else None
    if not isinstance(expected, list) or not expected or not isinstance(expected[0], str):
        raise ValueError(f"Frozen fixture {name} has no expected signature")
    target = root / "fixtures" / name / "bundle"
    shutil.copytree(source / "bundle", target, dirs_exist_ok=True)
    if _normalize(signature(target, expected[0])) != expected:
        raise ValueError(f"Frozen fixture {name} no longer produces its expected signature")
    return {"id": name, "signature": expected, "representative": f"fixtures/{name}/bundle"}


def run_fixture_replays(
    corpus: dict, fixtures: Path, work: Path, workers: int, image: str,
    replay: ReplayFn, signature: SignatureFn,
) -> dict:
    groups = defaultdict(list)
    for name, entry in sorted(corpus["fixtures"].items()):
        groups[entry["nightly_replays"]].append(name)
    results = []
    for repeats, names in sorted(groups.items()):
        root = work / f"k-{repeats:02d}"
        report = {"corpus": [_stage(fixtures, root, name, signature) for name in names]}
        replay(root, root / "replays.json", report, repeats, workers, image)
        for case in report["corpus"]:
            rows = [{**row, "bundle": f"{root.name}/{row['bundle']}"} for row in case["replays"]]
            results.append({"id": case["id"], "replays": rows})
    return {"schema_version": SCHEMA_VERSION, "fixtures": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, help="Results path (default: WORK/results.json)")
    args = parser.parse_args()
    try:
        corpus = load_corpus(args.corpus)
        if corpus["fixtures"]:
            from envfuzz.analysis import regression_signature, replay_corpus

            results = run_fixture_replays(corpus, args.fixtures, args.work, args.workers, args.image,
                                          replay_corpus, regression_signature)
        else:
            print("Required replay corpus is empty: nothing to replay.")
            results = {"schema_version": SCHEMA_VERSION, "fixtures": []}
    except (OSError, ValueError) as error:
        parser.error(str(error))
    output = args.output or args.work / "results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
