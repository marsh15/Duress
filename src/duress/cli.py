"""Duress CLI (M1 slice: run, sweep, targets)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer

from . import limits as limits_module
from .models import ScenarioResult
from .operations import OperationConfig, run_scenario
from .profiles import ProfileRegistry, load_seed, repo_root
from .provider_stub import StubServer
from .scenarios import build_scenario
from .store import duress_home, sweep

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Put your integrations under pressure. Keep the proof.",
)

EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}


def _root() -> Path:
    return repo_root(Path.cwd())


def _home() -> Path:
    return duress_home(_root())


def _scenario_params(
    template_id: str, copies: int, fault: str, latency_ms: int
) -> dict[str, Any]:
    if template_id == "S3":
        return {"copies": copies, "fault": fault, "latency_ms": latency_ms}
    return {}


def _print_result(result: ScenarioResult) -> None:
    typer.echo(f"scenario  {result.scenario_id}")
    typer.echo(
        f"verdict   {result.result.status}  "
        f"(valid={result.result.valid}, fail={result.result.fail}, "
        f"inconclusive={result.result.inconclusive}, trials={result.result.trials})"
    )
    if result.result.signature:
        typer.echo(
            "signature "
            f"{result.result.signature.check} {result.result.signature.kind} "
            f"{', '.join(result.result.signature.roles)}"
        )
    if result.result.reasons:
        typer.echo("reasons   " + ", ".join(result.result.reasons))
    typer.echo("")
    typer.echo(f"{'trial':<18}{'verdict':<14}{'reasons'}")
    for observation, verdict in zip(result.observations, result.verdicts):
        typer.echo(
            f"{observation.trial_id:<18}{verdict.status:<14}{', '.join(verdict.reasons)}"
        )


def _run_operation(
    target_id: str,
    template_id: str,
    params: dict[str, Any],
    policy_name: str,
) -> ScenarioResult:
    registry = ProfileRegistry.discover(_root())
    profile = registry.get(target_id)
    seed = load_seed(profile)
    scenario = build_scenario(template_id, params, profile.roles, seed)
    policy = limits_module.POLICIES.get(policy_name)
    if policy is None:
        raise typer.BadParameter(
            f"unknown policy {policy_name!r}; known: {', '.join(limits_module.POLICIES)}"
        )

    sweep(_home())
    with StubServer() as stub:
        config = OperationConfig(
            profile=profile,
            scenario=scenario,
            stub=stub,
            workdir_root=Path(_home()) / "tmp",
            policy=policy,
            home=_home(),
            keep_evidence=True,
        )
        Path(config.workdir_root).mkdir(parents=True, exist_ok=True)
        return asyncio.run(run_scenario(config))


@app.command("targets")
def targets_command() -> None:
    """List target profiles."""
    registry = ProfileRegistry.discover(_root())
    for profile in registry.all():
        typer.echo(
            f"{profile.target_id:<28}{profile.profile_id:<18}{profile.variant:<14}"
            f"contract v{profile.contract_version}  workers={profile.workers}"
        )


@app.command("run")
def run_command(
    target: str = typer.Argument(..., help="target id, e.g. razorpay-vulnerable"),
    scenario: str = typer.Option("S3", "--scenario", "-s"),
    copies: int = typer.Option(3, "--copies"),
    fault: str = typer.Option("rendezvous", "--fault"),
    latency_ms: int = typer.Option(200, "--latency-ms"),
    policy: str = typer.Option("explore", "--policy"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run one scenario against a target and print the aggregate verdict."""
    params = _scenario_params(scenario, copies, fault, latency_ms)
    try:
        result = _run_operation(target, scenario, params, policy)
    except (KeyError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        _print_result(result)
    raise typer.Exit(EXIT_CODES[result.result.status])


@app.command("sweep")
def sweep_command() -> None:
    """Kill process groups recorded by earlier trials whose start time matches."""
    result = sweep(_home())
    for info in result.killed:
        typer.echo(f"killed pgid={info.pgid} instance={info.instance}")
    for info in result.stale:
        typer.echo(f"stale  pgid={info.pgid} instance={info.instance}")
    if not result.killed and not result.stale:
        typer.echo("nothing to sweep")


def main() -> None:
    app()


if __name__ == "__main__":
    main()