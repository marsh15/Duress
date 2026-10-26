"""Duress CLI: targets, run, dna, replay, verify-fix, sweep, serve (C.13)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer

from . import limits as limits_module
from .models import ScenarioResult
from .replay import replay_exit_code
from .services import (
    Workspace,
    build_dna,
    dna_index,
    load_dna_document,
    replay,
    run_target_scenario,
    verify_fix,
)
from .store import sweep

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Put your integrations under pressure. Keep the proof.",
)
dna_app = typer.Typer(no_args_is_help=True, help="Failure DNA artifacts.")
app.add_typer(dna_app, name="dna")

EXIT_CODES = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}
VERIFY_CODES = {"VERIFIED": 0, "NOT_VERIFIED": 1, "INCONCLUSIVE": 2, "REJECTED": 4}


def _workspace() -> Workspace:
    return Workspace.discover(Path.cwd())


def scenario_params(
    template_id: str, copies: int, fault: str, latency_ms: int
) -> dict[str, Any]:
    if template_id == "S3":
        return {"copies": copies, "fault": fault, "latency_ms": latency_ms}
    if template_id == "S5":
        return {"fault": fault, "latency_ms": latency_ms}
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


@app.command("targets")
def targets_command() -> None:
    """List target profiles."""
    workspace = _workspace()
    for profile in workspace.registry.all():
        typer.echo(
            f"{profile.target_id:<32}{profile.profile_id:<18}{profile.variant:<18}"
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
    params = scenario_params(scenario, copies, fault, latency_ms)
    try:
        result = asyncio.run(
            run_target_scenario(
                _workspace(), target_id=target, template_id=scenario, params=params, policy=policy
            )
        )
    except (KeyError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        _print_result(result)
    raise typer.Exit(EXIT_CODES[result.result.status])


@dna_app.command("build")
def dna_build_command(
    target: str = typer.Argument(..., help="a target expected to fail"),
    scenario: str = typer.Option("S3", "--scenario", "-s"),
    copies: int = typer.Option(3, "--copies"),
    fault: str = typer.Option("rendezvous", "--fault"),
    latency_ms: int = typer.Option(200, "--latency-ms"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Characterise a real failure and write portable Failure DNA."""
    params = scenario_params(scenario, copies, fault, latency_ms)
    try:
        document, characterisation = asyncio.run(
            build_dna(
                _workspace(),
                target_id=target,
                template_id=scenario,
                params=params,
            )
        )
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except Exception as exc:
        typer.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(json.dumps(document.model_dump(mode="json"), indent=2))
        return
    signature = document.signature()
    typer.echo(f"dna_id    {document.dna_id}")
    typer.echo(f"signature {signature.check} {signature.kind} {', '.join(signature.roles)}")
    typer.echo(
        f"p0        {characterisation.p0} ({characterisation.fail}/{characterisation.valid} valid)"
    )
    typer.echo(f"release   {document.reproducer['release_condition']}")
    typer.echo(f"control   {document.reproducer['control_level']}")


@dna_app.command("list")
def dna_list_command(json_output: bool = typer.Option(False, "--json")) -> None:
    """List stored DNA artifacts."""
    items = dna_index(_workspace())
    if json_output:
        typer.echo(json.dumps(items, indent=2))
        return
    if not items:
        typer.echo("no DNA artifacts")
        return
    typer.echo(f"{'dna_id':<20}{'signature':<44}{'p0':<8}{'reduced'}")
    for item in items:
        signature = item.get("signature") or {}
        key = f"{signature.get('check')}/{signature.get('kind')}"
        typer.echo(
            f"{str(item.get('dna_id')):<20}{key:<44}{str(item.get('p0')):<8}"
            f"{'yes' if item.get('reduction') else 'no'}"
        )


@app.command("replay")
def replay_command(
    dna: str = typer.Argument(..., help="DNA id"),
    target: str | None = typer.Option(None, "--target", help="target id (default: from DNA)"),
    policy: str = typer.Option("replay", "--policy"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Replay a DNA against a fresh target process."""
    workspace = _workspace()
    try:
        document = load_dna_document(workspace, dna)
    except FileNotFoundError:
        typer.echo(f"error: no DNA {dna!r}", err=True)
        raise typer.Exit(3) from None
    target_id = target or str(document.provenance.get("target_id") or "razorpay-vulnerable")
    run = asyncio.run(
        replay(workspace, dna_id=dna, target_id=target_id, policy=policy)
    )
    if json_output:
        typer.echo(json.dumps(run.outcome.model_dump(mode="json"), indent=2))
    else:
        outcome = run.outcome
        typer.echo(f"dna       {dna}")
        typer.echo(f"target    {target_id}")
        typer.echo(f"outcome   {outcome.outcome}")
        if outcome.reason:
            typer.echo(f"reason    {outcome.reason}")
        typer.echo(
            f"trials    valid={outcome.valid} fail={outcome.fail} "
            f"inconclusive={outcome.inconclusive}"
        )
        if outcome.outcome == "REJECTED" and run.preflight:
            typer.echo(f"preflight {run.preflight}")
    raise typer.Exit(replay_exit_code(run.outcome))


@app.command("verify-fix")
def verify_fix_command(
    dna: str = typer.Argument(..., help="DNA id"),
    vulnerable: str = typer.Option("razorpay-vulnerable", "--vulnerable"),
    candidate: str = typer.Option("razorpay-fixed", "--candidate"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Empirically verify a candidate against the characterised failure."""
    run = asyncio.run(
        verify_fix(
            _workspace(),
            dna_id=dna,
            vulnerable_target_id=vulnerable,
            candidate_target_id=candidate,
        )
    )
    outcome = run.outcome
    if json_output:
        typer.echo(json.dumps(outcome.model_dump(mode="json"), indent=2))
    else:
        typer.echo(f"dna        {dna}")
        typer.echo(f"control    {vulnerable}")
        typer.echo(f"candidate  {candidate}")
        typer.echo(f"outcome    {outcome.outcome}")
        if outcome.reason:
            typer.echo(f"reason     {outcome.reason}")
        if outcome.control:
            typer.echo(
                "control    "
                f"k={outcome.control.get('same_signature_failures')} "
                f"v={outcome.control.get('valid')} p_hat={outcome.control.get('p_hat')}"
            )
        if outcome.candidate:
            typer.echo(
                "candidate  "
                f"valid={outcome.candidate.get('valid')} "
                f"n_c={outcome.candidate.get('required_valid')}"
            )
        if outcome.statement:
            typer.echo("")
            typer.echo(outcome.statement)
    raise typer.Exit(VERIFY_CODES[outcome.outcome])


@app.command("sweep")
def sweep_command() -> None:
    """Kill process groups recorded by earlier trials whose start time matches."""
    result = sweep(_workspace().home)
    for info in result.killed:
        typer.echo(f"killed pgid={info.pgid} instance={info.instance}")
    for info in result.stale:
        typer.echo(f"stale  pgid={info.pgid} instance={info.instance}")
    for info in result.unresolved:
        typer.echo(
            f"unresolved pgid={info.pgid} instance={info.instance} "
            "(still alive; pidfile kept)"
        )
    if not result.killed and not result.stale and not result.unresolved:
        typer.echo("nothing to sweep")


@app.command("serve")
def serve_command(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Serve the API and web application."""
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@app.command("report")
def report_command(
    investigation: str = typer.Argument("showcase", help="Stored record id, or showcase"),
    output: Path = typer.Option(Path("duress-report.html"), "--output", "-o"),
) -> None:
    """Export an existing investigation record as a self-contained HTML report."""
    from .records import load_investigation_record
    from .store import atomic_write_text
    from .web import render_report

    try:
        record = load_investigation_record(_workspace(), investigation)
    except FileNotFoundError:
        typer.echo(f"error: unknown investigation {investigation!r}", err=True)
        raise typer.Exit(3) from None
    atomic_write_text(output, render_report(record))
    typer.echo(f"report {output.resolve()}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
