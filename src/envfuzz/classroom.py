"""Validate data-only submissions and run organizer-owned evaluation containers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .oracle import touches_boundary
from .scenario import Scenario, _radius, load_scenario
from .variants import Edit, EditBudget, apply_edits
from .workers import WorkerLimits, run_worker


class Challenge(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    id: str
    robot: Literal["turtlebot3_waffle"] = "turtlebot3_waffle"
    difficulty: str
    scenario: Path
    baseline: Path
    oracle: Path | None = None
    params: Path | None = None
    profile: Path | None = None
    budget: EditBudget = Field(default_factory=EditBudget)
    max_submission_bytes: int = Field(default=32768, ge=100, le=1000000)
    timeout: float = Field(default=35, gt=0)
    watchdog: float = Field(default=80, gt=0)
    repeats: int = Field(default=3, ge=1, le=20)
    cpus: float = Field(default=2.0, ge=0.1)
    memory: str = "1536m"


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    challenge: str
    team: str = Field(min_length=1, max_length=80)
    edits: list[Edit] = Field(max_length=100)


def check_submission(manifest: Path, submission: Path) -> tuple[Challenge, Submission, Scenario, str]:
    challenge = Challenge.model_validate(yaml.safe_load(manifest.read_text(encoding="utf-8")))
    if submission.suffix.lower() != ".json":
        raise ValueError("A submission must be one JSON file")
    with submission.open("rb") as handle:
        data = handle.read(challenge.max_submission_bytes + 1)
    if len(data) > challenge.max_submission_bytes:
        raise ValueError("Submission exceeds the byte limit")
    candidate = Submission.model_validate_json(data)
    if candidate.challenge != challenge.id:
        raise ValueError("Submission targets a different classroom challenge")
    base = load_scenario(manifest.parent / challenge.scenario)
    constraints = {**base.challenge, "require_reachable": True, "forbid_restricted_objects": True}
    variant = apply_edits(base.model_copy(update={"challenge": constraints}), candidate.edits, challenge.budget)
    for obstacle in variant.objects:
        for zone in variant.restricted_zones:
            if touches_boundary(obstacle.pose.x, obstacle.pose.y, zone.polygon, _radius(obstacle)):
                raise ValueError(f"{obstacle.id}: intersects forbidden placement region {zone.id}")
    return challenge, candidate, variant, hashlib.sha256(data).hexdigest()


def evaluate_submission(manifest: Path, submission: Path, output: Path, image: str) -> dict:
    challenge, candidate, scenario, digest = check_submission(manifest, submission)
    base = manifest.resolve().parent
    baseline = base / challenge.baseline
    if not baseline.is_file():
        raise ValueError("The organizer baseline is missing")
    output.mkdir(parents=True, exist_ok=False)
    runs = []
    for index in range(1, challenge.repeats + 1):
        bundle = output / f"repeat-{index:03d}" / "bundle"
        result = run_worker(
            scenario, bundle, challenge.timeout, challenge.watchdog,
            WorkerLimits(image, challenge.cpus, challenge.memory),
            baseline=baseline, params=(base / challenge.params if challenge.params else None),
            oracle=(base / challenge.oracle if challenge.oracle else None),
            profile=(base / challenge.profile if challenge.profile else None), read_only=True,
        )
        runs.append({"bundle": bundle.relative_to(output).as_posix(),
                     "status": result.status, "classification": result.classification,
                     "sim_duration_s": result.sim_duration, "observed_tiers": result.tiers_hit})
    completed = [run["sim_duration_s"] for run in runs if run["status"] == "completed"]
    valid = sum(run["classification"] == "behavior" for run in runs)
    report = {
        "challenge": challenge.id, "team": candidate.team, "submission_sha256": digest,
        "scenario_hash": scenario.content_hash(), "verified": valid == challenge.repeats,
        "successes": len(completed), "valid_runs": valid, "expected_runs": challenge.repeats,
        "median_success_sim_s": statistics.median(completed) if completed else None,
        "policy": "All repeats must be valid. Rank by completed missions and then median success time.",
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "runs": runs,
    }
    (output / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def write_scoreboard(evaluations: list[Path], output: Path) -> None:
    boards: dict[tuple[str, str, str], dict] = {}
    for path in evaluations:
        result = json.loads(path.read_text(encoding="utf-8"))
        key = (result["challenge"], result["manifest_sha256"], result["baseline_sha256"])
        board = boards.setdefault(key, {})
        if result["verified"]:
            board[result["submission_sha256"]] = {
                "team": result["team"], "successes": result["successes"],
                "median_success_sim_s": result["median_success_sim_s"],
                "evaluation": str(path), "submission_sha256": result["submission_sha256"],
            }
    rows = [
        {"challenge": challenge, "manifest_sha256": manifest, "baseline_sha256": baseline,
         "entries": sorted(entries.values(), key=lambda row: (
             -row["successes"], row["median_success_sim_s"] if row["median_success_sim_s"] is not None else float("inf"),
             row["team"], row["submission_sha256"],
         ))}
        for (challenge, manifest, baseline), entries in sorted(boards.items())
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
