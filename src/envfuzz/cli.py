"""Command-line interface for simulator-free and container-backed workflows."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Optional

import typer
from pydantic import ValidationError

from .analysis import analyze_campaign, replay_corpus
from .campaign import prepare_campaign, run_campaign, run_parallel_campaign
from .campaign_index import render_campaign_report
from .classroom import check_submission, evaluate_submission, write_scoreboard
from .materials import reference_rays
from .oracle import save_baseline
from .report import render_diagnostics, render_report
from .results import EpisodeResult, runtime_versions
from .scenario import SCENARIO_JSON_SCHEMA, Scenario, compile_sdf, load_scenario, validate_scenario
from .scoring import baseline_from_trials
from .sensor_fixtures import check_sensor_fixtures
from .fuzzer import fuzz
from .gazebo import run_gazebo
from .minimize import minimize_run
from .runner import EpisodeRunner
from .workers import WorkerLimits

app = typer.Typer(name="envfuzz", no_args_is_help=True)
scenario_app = typer.Typer()
classroom_app = typer.Typer(no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")
app.add_typer(classroom_app, name="classroom")


@scenario_app.command("validate")
def scenario_validate(path: Path, schema: bool = typer.Option(False, "--schema")) -> None:
    if schema:
        typer.echo(json.dumps(SCENARIO_JSON_SCHEMA, indent=2))
        return
    try:
        scenario = load_scenario(path)
        violations = validate_scenario(scenario)
    except (ValidationError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    if violations:
        raise typer.BadParameter("\n".join(violations))
    typer.echo(f"valid scenario: {scenario.content_hash()}")


@app.command("compile")
def compile_command(path: Path, output: Path = Path("world.sdf")) -> None:
    output.write_text(compile_sdf(load_scenario(path)), encoding="utf-8")
    typer.echo(str(output))


@app.command("surface-probe")
def surface_probe(path: Path) -> None:
    """Report fixed-angle reference rays for a static material fixture."""
    try:
        scenario = load_scenario(path)
        violations = validate_scenario(scenario)
        if violations:
            raise ValueError("; ".join(violations))
        if scenario.events or scenario.actors:
            raise ValueError("A surface probe requires a static fixture")
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(reference_rays(scenario, scenario.start, (-0.2, -0.1, 0.0, 0.1, 0.2)), indent=2))


@app.command("doctor")
def doctor() -> None:
    typer.echo(json.dumps(runtime_versions(), indent=2, sort_keys=True))


@classroom_app.command("check")
def classroom_check(manifest: Path, submission: Path) -> None:
    try:
        _, _, scenario, _ = check_submission(manifest, submission)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Valid submission. Scenario {scenario.content_hash()}")


@classroom_app.command("check-sensor-fixtures")
def classroom_check_sensor_fixtures(fixtures: Path) -> None:
    try:
        report = check_sensor_fixtures(fixtures)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(report, indent=2))
    if not report["passed"]:
        raise typer.Exit(code=2)


@classroom_app.command("evaluate")
def classroom_evaluate(
    manifest: Path, submission: Path, output: Path = Path("runs/evaluation"),
    image: str = typer.Option("envfuzz:jazzy"),
) -> None:
    try:
        report = evaluate_submission(manifest, submission, output, image)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"{report['successes']} successes across {report['valid_runs']} valid runs. {output / 'evaluation.json'}")
    if not report["verified"]:
        raise typer.Exit(code=2)


@classroom_app.command("scoreboard")
def classroom_scoreboard(
    evaluations: list[Path], output: Path = Path("runs/scoreboard.json"),
) -> None:
    try:
        write_scoreboard(evaluations, output)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(str(output))


@app.command("run")
def run_command(
    path: Path,
    output: Path = Path("runs/episode"),
    timeout: float = typer.Option(120.0, min=0.1),
    watchdog: float = typer.Option(300.0, min=0.1),
    smoke: bool = typer.Option(False, "--smoke", help="Write a bundle without launching ROS or Gazebo."),
    params_file: Optional[Path] = typer.Option(None, exists=True, dir_okay=False),
    baseline_file: Optional[Path] = typer.Option(None, "--baseline", exists=True, dir_okay=False),
    oracle_file: Optional[Path] = typer.Option(None, "--oracle", exists=True, dir_okay=False),
    profile_file: Optional[Path] = typer.Option(None, "--profile", exists=True, dir_okay=False),
) -> None:
    scenario = load_scenario(path)
    try:
        if smoke:
            if any(path is not None for path in (params_file, baseline_file, oracle_file, profile_file)):
                raise typer.BadParameter("Nav2 settings, baselines, oracle settings, and profiles require Gazebo")
            result = EpisodeRunner(watchdog_seconds=watchdog).run(
                scenario, output=output, sim_timeout=timeout,
            )
        else:
            result = run_gazebo(scenario, output, timeout, watchdog, params_file, baseline_file, oracle_file, profile_file)
    except FileExistsError as error:
        raise typer.BadParameter(f"Choose a new output directory: {output}") from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _report_run(result, output)


@app.command("replay")
def replay(
    bundle: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Path = Path("runs/replay"),
    profile: Optional[Path] = typer.Option(None, exists=True, dir_okay=False),
) -> None:
    config_path = bundle / "run.json"
    if not config_path.is_file():
        raise typer.BadParameter("Replay requires a Gazebo run bundle")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    params = bundle / "nav2.yaml"
    baseline_file = bundle / "baseline.json"
    oracle_file = bundle / "oracle.yaml"
    stored_profile = bundle / "profile.yaml"
    profile = profile or (stored_profile if stored_profile.is_file() else None)
    run_command(
        bundle / "scenario.json", output, config["timeout"], config["watchdog"],
        False, params if params.is_file() else None, baseline_file if baseline_file.is_file() else None,
        oracle_file if oracle_file.is_file() else None,
        profile,
    )


def _report_run(result: EpisodeResult, output: Path) -> None:
    typer.echo(f"{result.status}: {output}")
    if result.baseline_sha256 is not None:
        typer.echo(f"score={result.score} tiers={','.join(result.tiers_hit) or '-'}")
    if result.status != "completed":
        if result.reason:
            typer.echo(result.reason, err=True)
        raise typer.Exit(code=2)


def _run_with_retries(scenario, output, timeout, watchdog, params_file, baseline_file=None, profile_file=None):
    for retry in range(3):
        bundle = output if retry == 0 else output.with_name(f"{output.name}-retry-{retry}")
        result = run_gazebo(scenario, bundle, timeout, watchdog, params_file, baseline_file,
                            profile_file=profile_file)
        if result.status not in {"watchdog", "simulator_failed"} or retry == 2:
            return result, bundle
        typer.echo(f"{result.status}: {bundle}. Retrying.", err=True)


@app.command("report")
def report_command(
    bundle: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Optional[Path] = typer.Option(None),
    strategy: Optional[str] = typer.Option(None),
    tier: Optional[str] = typer.Option(None),
    scenario_hash: Optional[str] = typer.Option(None),
    minimum_score: Optional[int] = typer.Option(None, "--min-score"),
) -> None:
    campaign = (bundle / "plan.json").is_file()
    target = output if output is not None else bundle / ("report.html" if campaign else "trajectory.svg")
    try:
        if campaign:
            render_campaign_report(bundle, target, strategy=strategy, tier=tier,
                                   scenario_hash=scenario_hash, minimum_score=minimum_score)
        else:
            render_report(bundle, target)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(str(target))


@app.command("show")
def show_run(
    bundle: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Optional[Path] = typer.Option(None),
) -> None:
    target = output if output is not None else bundle / "diagnostics.html"
    try:
        render_diagnostics(bundle, target)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(str(target))


@app.command("analyze")
def analyze(
    campaign: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Optional[Path] = typer.Option(None),
    replays: int = typer.Option(0, min=0, max=20),
    workers: int = typer.Option(1, min=1, max=8),
    image: str = typer.Option("envfuzz:jazzy"),
) -> None:
    target = output if output is not None else campaign / "analysis.json"
    try:
        report = analyze_campaign(campaign, target)
        if replays:
            replay_corpus(campaign, target, report, replays, workers, image)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"{report['shared_budget']} episodes per strategy. {target}")


@app.command("minimize")
def minimize(
    bundle: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Path = Path("runs/minimized"),
    repeats: int = typer.Option(3, min=1),
    minimum_rate: float = typer.Option(2 / 3, min=0, max=1),
) -> None:
    try:
        report = minimize_run(bundle, output, repeats, minimum_rate)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"{report['remaining_objects']} objects. {output / 'minimization.json'}")


@app.command("baseline")
def baseline(
    path: Optional[Path] = typer.Argument(None, exists=True, dir_okay=False),
    n: int = typer.Option(10, min=1),
    output: Path = Path("runs/baseline"),
    timeout: float = typer.Option(120.0, min=0.1),
    watchdog: float = typer.Option(300.0, min=0.1),
    params_file: Optional[Path] = typer.Option(None, exists=True, dir_okay=False),
    profile_file: Optional[Path] = typer.Option(None, "--profile", exists=True, dir_okay=False),
) -> None:
    scenario = load_scenario(path) if path is not None else Scenario()
    try:
        output.mkdir(parents=True)
    except FileExistsError as error:
        raise typer.BadParameter(f"Choose a new output directory: {output}") from error
    durations, trials = [], []
    for index in range(1, n + 1):
        bundle = output / f"trial-{index:04d}"
        result, bundle = _run_with_retries(scenario, bundle, timeout, watchdog, params_file,
                                           profile_file=profile_file)
        _report_run(result, bundle)
        trials.append(bundle)
        durations.append(result.sim_duration)
    try:
        data = baseline_from_trials(scenario, trials, durations)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    save_baseline(data, output / "baseline.json")
    typer.echo(str(output / "baseline.json"))


@app.command("campaign")
def campaign_command(
    manifest: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path = Path("runs/campaign"),
    plan_only: bool = typer.Option(False, "--plan-only"),
    workers: Optional[int] = typer.Option(None, min=1),
    image: str = typer.Option("envfuzz:jazzy"),
    worker_cpus: float = typer.Option(2.0, min=0.1),
    worker_memory: str = typer.Option("1536m"),
    retries: int = typer.Option(2, min=0),
) -> None:
    try:
        config, cases = prepare_campaign(manifest, output)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"{len(cases)} cases in {output / 'plan.json'}")
    if not plan_only:
        try:
            if workers is None:
                complete = run_campaign(config, cases, output)
            else:
                complete = run_parallel_campaign(
                    config, cases, output, workers, WorkerLimits(image, worker_cpus, worker_memory), retries,
                )
        except ValueError as error:
            raise typer.BadParameter(str(error)) from error
        if not complete:
            typer.echo(f"Campaign paused after an incomplete or infrastructure run. See {output / 'results.csv'}.",
                       err=True)
            raise typer.Exit(code=2)
        typer.echo(str(output / "results.csv"))


@app.command("fuzz")
def fuzz_command(
    budget: int = typer.Option(..., min=0),
    seed: int = typer.Option(0),
    output: Path = Path("runs/results.jsonl"),
    smoke: bool = typer.Option(False, "--smoke"),
    baseline_file: Optional[Path] = typer.Option(None, "--baseline", exists=True, dir_okay=False),
    scenario_file: Optional[Path] = typer.Option(None, "--scenario", exists=True, dir_okay=False),
    params_file: Optional[Path] = typer.Option(None, exists=True, dir_okay=False),
    timeout: float = typer.Option(120.0, min=0.1),
    watchdog: float = typer.Option(300.0, min=0.1),
) -> None:
    if not smoke and baseline_file is None:
        raise typer.BadParameter("Live fuzzing requires --baseline. Run envfuzz baseline first.")
    base = load_scenario(scenario_file) if scenario_file is not None else Scenario()
    run_dir = None
    if not smoke and budget:
        output.parent.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix=f"fuzz-{seed}-", dir=output.parent))
        typer.echo(f"Episode bundles: {run_dir}")
    index = 0

    def execute(scenario):
        nonlocal index
        if run_dir is None:
            return EpisodeResult(status="completed", classification="behavior")
        index += 1
        result, _ = _run_with_retries(
            scenario, run_dir / f"case-{index:04d}", timeout, watchdog, params_file, baseline_file,
        )
        return result

    try:
        results, rejected = fuzz(budget, seed, execute, output, base=base)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    for index, result in sorted(enumerate(results), key=lambda item: item[1].score, reverse=True):
        typer.echo(f"{index + 1}: score={result.score} tiers={','.join(result.tiers_hit) or '-'}")
    completed = sum(result.classification == "behavior" for result in results)
    typer.echo(f"completed={completed} rejected={rejected}")
    if completed < budget:
        typer.echo(results[-1].reason or results[-1].status, err=True)
        raise typer.Exit(code=2)
