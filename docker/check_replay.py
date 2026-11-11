"""Report nightly replay agreement and end-to-end simulation speed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median


def check_campaign(
    root: Path, minimum_real_time_factor: float, required: dict[str, float],
) -> dict:
    analysis = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
    cases = {case["representative"]: case for case in analysis["corpus"]}
    failures = []
    for name, threshold in required.items():
        evidence = cases.get(name, {})
        rate = evidence.get("reproduction_rate")
        if evidence.get("valid_replays", 0) < 20 or rate is None or rate < threshold:
            failures.append(f"{name}: insufficient replay agreement")
    rates, infrastructure = [], 0
    for path in root.glob("case-*/attempt-*/bundle/result.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        if result["classification"] == "infrastructure":
            infrastructure += 1
        if result["classification"] == "behavior" and result.get("real_time_factor") is not None:
            rates.append(result["real_time_factor"])
    speed = median(rates) if rates else None
    if speed is None or speed < minimum_real_time_factor:
        failures.append("Median end-to-end real-time factor is below its configured floor")
    return {"median_end_to_end_real_time_factor": speed, "valid_campaign_rates": len(rates),
            "campaign_infrastructure_attempts": infrastructure,
            "diagnostic_replay_cases": len(cases), "required_replay_cases": len(required),
            "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--minimum-real-time-factor", type=float, required=True)
    parser.add_argument("--required", type=Path, help="JSON object mapping representative paths to agreement floors")
    args = parser.parse_args()
    required = json.loads(args.required.read_text(encoding="utf-8")) if args.required else {}
    if any(not 0.95 <= value <= 1 for value in required.values()):
        parser.error("Required replay agreement floors must be in [0.95, 1]")
    report = check_campaign(args.campaign, args.minimum_real_time_factor, required)
    output = json.dumps(report, indent=2) + "\n"
    (args.campaign / "verification.json").write_text(output, encoding="utf-8")
    print(output, end="")
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
