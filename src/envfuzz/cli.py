"""Command-line interface for simulator-free and container-backed workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError

from .oracle import calibrate, save_baseline
from .results import runtime_versions
from .scenario import SCENARIO_JSON_SCHEMA, compile_sdf, load_scenario, validate_scenario
from .fuzzer import fuzz
from .runner import EpisodeRunner

app = typer.Typer(name="envfuzz", no_args_is_help=True)
scenario_app = typer.Typer()
app.add_typer(scenario_app, name="scenario")


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


@app.command("doctor")
def doctor() -> None:
    typer.echo(json.dumps(runtime_versions(), indent=2, sort_keys=True))


@app.command("run")
def run_command(
    path: Path,
    output: Path = Path("runs/episode"),
    timeout: float = typer.Option(120.0, min=0.1),
    watchdog: float = typer.Option(300.0, min=0.1),
) -> None:
    scenario = load_scenario(path)
    result = EpisodeRunner(watchdog_seconds=watchdog).run(
        scenario, output=output, sim_timeout=timeout
    )
    typer.echo(f"{result.status}: {output}")
    if result.status not in {"completed"}:
        raise typer.Exit(code=2)


@app.command("baseline")
def baseline(n: int = typer.Option(10, min=1), output: Path = Path("baseline.json")) -> None:
    data = calibrate([0.0] * n, {"world": "apartment", "mission": "default"})
    save_baseline(data, output)
    typer.echo(str(output))


@app.command("fuzz")
def fuzz_command(
    budget: int = typer.Option(..., min=0),
    seed: int = typer.Option(0),
    output: Path = Path("runs/results.jsonl"),
) -> None:
    def execute(scenario):
        from .results import EpisodeResult
        return EpisodeResult(status="completed", classification="behavior")

    results, rejected = fuzz(budget, seed, execute, output)
    for index, result in sorted(enumerate(results), key=lambda item: item[1].score, reverse=True):
        typer.echo(f"{index + 1}: score={result.score} tiers={','.join(result.tiers_hit) or '-'}")
    typer.echo(f"completed={len(results)} rejected={rejected}")
