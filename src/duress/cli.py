"""Duress CLI: targets, run, dna, replay, verify-fix, investigate, reduce,
differential, sweep, serve (C.13)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
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
    workspace = Workspace.discover(Path.cwd())
    # Resource discovery can resolve an installed wheel or DURESS_ROOT while
    # runtime records remain in the invoking directory (or DURESS_HOME).
    return replace(workspace, root=workspace.registry.root)


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
def targets_command(config: Path | None = typer.Option(None,"--config"),
                    json_output: bool = typer.Option(False,"--json")) -> None:
    """List target profiles."""
    if config:
        from .external.manifest import load_project
        from .external.commands import emit
        project=load_project(config)
        emit({"project":project.manifest.duress.project,"target_id":project.manifest.target.id,
              "adapter":"external@1","mode":project.manifest.target.mode,
              "contract":f"{project.contract.id}@{project.contract.version}",
              "scenarios":[s.id for s in project.manifest.scenarios]},json_output=json_output)
        return
    workspace = _workspace()
    if json_output:
        typer.echo(json.dumps([{"target_id":p.target_id,"profile_id":p.profile_id,"variant":p.variant} for p in workspace.registry.all()],indent=2))
        return
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
def dna_list_command(json_output: bool = typer.Option(False, "--json"),
                     config: Path | None = typer.Option(None,"--config")) -> None:
    """List stored DNA artifacts."""
    if config:
        from .external.manifest import load_project
        from .external.storage import ProjectStore
        items=[r["payload"] for r in ProjectStore(load_project(config).manifest.duress.project).list("dna")]
        typer.echo(json.dumps(items,indent=2))
        return
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
    config: Path | None = typer.Option(None,"--config"),
) -> None:
    """Replay a DNA against a fresh target process."""
    if config:
        _external_operation("replay",config,dna,json_output=json_output)
        return
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
    config: Path | None = typer.Option(None,"--config"),
    candidate_config: Path | None = typer.Option(None,"--candidate-config"),
) -> None:
    """Empirically verify a candidate against the characterised failure."""
    if config:
        if not candidate_config:
            raise typer.BadParameter("external verification requires --candidate-config")
        _external_operation("verify",config,dna,candidate_config=candidate_config,json_output=json_output)
        return
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


INVESTIGATION_CODES = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}
REDUCE_CODES = {
    "REDUCED": 0,
    "NOT_REDUCED": 0,
    "SKIPPED": 2,
    "INCONCLUSIVE": 2,
    "REJECTED": 4,
}


@app.command("investigate")
def investigate_command(
    target: str | None = typer.Argument(None, help="target id, e.g. razorpay-vulnerable"),
    mode: str = typer.Option("cached", "--mode", help="live | cached | exhaustive"),
    reduce: bool = typer.Option(False, "--reduce", help="reduce each new DNA (M4)"),
    wall_cap_s: float | None = typer.Option(None, "--wall-cap-s", help="override the wall cap"),
    json_output: bool = typer.Option(False, "--json"),
    config: Path | None = typer.Option(None,"--config"),
    scenario: str | None = typer.Option(None,"--scenario"),
    output: Path | None = typer.Option(None,"--output"),
) -> None:
    """Investigate a target: hypotheses, confirming runs, Failure DNA."""
    if config:
        from .external.services import investigate as investigate_external
        from .external.commands import emit
        from .external.reporting import export_run
        if mode != "exhaustive":
            raise typer.BadParameter("external contracts currently support --mode exhaustive")
        try:
            record=asyncio.run(investigate_external(config,scenario_id=scenario))
            export_run(record,output=output)
        except (ValueError,OSError,ImportError) as exc:
            typer.echo(f"error: {exc}",err=True)
            raise typer.Exit(3)
        emit(record,json_output)
        raise typer.Exit(EXIT_CODES[record["verdict"]])
    if not target:
        raise typer.BadParameter("target is required unless --config is supplied")
    from .orchestrator import MODES
    from .services import investigate

    if mode not in MODES:
        typer.echo(f"error: --mode must be one of {', '.join(MODES)}", err=True)
        raise typer.Exit(3)
    workspace = _workspace()
    try:
        workspace.registry.get(target)
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    record = asyncio.run(
        investigate(workspace, target_id=target, mode=mode, reduce=reduce, wall_cap_s=wall_cap_s)
    )
    if json_output:
        typer.echo(json.dumps(record, indent=2))
    else:
        typer.echo(f"investigation {record['investigation_id']}  ({record['status']})")
        typer.echo(f"mode          requested={record['mode_requested']} effective={record['mode_effective']}")
        if record.get("fallback_reason"):
            typer.echo(f"fallback      {record['fallback_reason']}")
        hypotheses = record.get("hypotheses") or []
        if hypotheses:
            typer.echo(f"hypotheses    {len(hypotheses)} (usable {record.get('usable_hypotheses')})")
            for item in hypotheses:
                status = item["status"]
                typer.echo(
                    f"  [{item['draft']['id']}] citation={status['citation']} "
                    f"structure={status['structure']} linkage={status['linkage']} "
                    f"outcome={status['outcome']}  (model text) {item['draft']['title']}"
                )
        typer.echo("")
        for entry in record.get("scenarios") or []:
            result = entry.get("result") or {}
            typer.echo(
                f"{entry['scenario_id']:<52}{str(result.get('status') or entry['status']):<14}"
                f"valid={result.get('valid')} fail={result.get('fail')}"
            )
        for finding in record.get("findings") or []:
            signature = finding["signature"]
            typer.echo(
                f"finding  {signature['check']} {signature['kind']} {','.join(signature['roles'])}"
                f"  dna={finding.get('dna_id') or finding.get('dna_error')}"
            )
        for item in record.get("reductions") or []:
            typer.echo(f"reduction {item.get('original_dna_id')}: {item.get('outcome')} {item.get('claim') or item.get('reason') or ''}")
        typer.echo(f"trials        {record.get('trials_used')} / {record.get('trial_cap')}")
        typer.echo(f"verdict       {record.get('verdict')}")
    if record["status"] != "completed":
        raise typer.Exit(2)
    raise typer.Exit(INVESTIGATION_CODES.get(record.get("verdict") or "INCONCLUSIVE", 2))


@app.command("reduce")
def reduce_command(
    dna: str = typer.Argument(..., help="DNA id"),
    target: str | None = typer.Option(None, "--target", help="target id (default: from DNA)"),
    black_box: bool = typer.Option(True, "--black-box", help="characterise without faults (required by C.11)"),
    json_output: bool = typer.Option(False, "--json"),
    config: Path | None = typer.Option(None,"--config"),
) -> None:
    """Reduce a DNA by single-delivery removal (bounded, release-preserving)."""
    if config:
        _external_operation("reduce",config,dna,json_output=json_output)
        return
    from .services import reduce_dna, reduction_summary

    workspace = _workspace()
    try:
        load_dna_document(workspace, dna)
    except FileNotFoundError:
        typer.echo(f"error: no DNA {dna!r}", err=True)
        raise typer.Exit(3) from None
    try:
        run = asyncio.run(reduce_dna(workspace, dna_id=dna, target_id=target, black_box=black_box))
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    summary = reduction_summary(run)
    if json_output:
        typer.echo(json.dumps({**run.to_json(), "summary": summary}, indent=2))
    else:
        typer.echo(f"reduction  {summary['reduction_id']}")
        typer.echo(f"dna        {dna}")
        typer.echo(f"outcome    {run.outcome}")
        if run.reason:
            typer.echo(f"reason     {run.reason}")
        if summary.get("claim"):
            typer.echo(f"claim      {summary['claim']}")
        if run.result is not None:
            typer.echo(
                f"deliveries {', '.join(run.result.original_deliveries)} -> "
                f"{', '.join(run.result.reduced_deliveries)}"
            )
            for candidate in run.result.candidates:
                typer.echo(
                    f"  -{candidate.removed:<6}{candidate.verdict:<14}"
                    f"{', '.join(candidate.inadmissible_reasons or candidate.reasons)}"
                )
        if run.revalidation:
            typer.echo(
                f"revalidate p={run.revalidation.get('p')} valid={run.revalidation.get('valid')} "
                f"accepted={run.revalidation.get('accepted')}"
            )
        if summary.get("reduced_dna_id"):
            typer.echo(f"reduced    {summary['reduced_dna_id']}")
        typer.echo(f"primary    {summary.get('primary_dna_id')}")
    raise typer.Exit(REDUCE_CODES.get(run.outcome, 2))


@app.command("differential")
def differential_command(
    targets: list[str] = typer.Option(None, "--target", help="repeatable; default vulnerable, naive_fix, fixed"),
    policy: str = typer.Option("explore", "--policy", help="explore | matrix"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run S3-rendezvous, S5 and S9 across variants and compare with plan E.1."""
    from .services import differential

    if policy not in {"explore", "matrix"}:
        typer.echo("error: --policy must be explore or matrix", err=True)
        raise typer.Exit(3)
    try:
        record = asyncio.run(differential(_workspace(), target_ids=targets or None, policy=policy))
    except KeyError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    if json_output:
        typer.echo(json.dumps(record, indent=2))
    else:
        typer.echo(f"differential {record['differential_id']}  policy={policy}")
        for cell in record["cells"]:
            mark = {True: "ok", False: "UNEXPECTED", None: "-"}[cell["agrees"]]
            typer.echo(
                f"{cell['scenario_id']:<48}{cell['target_id']:<22}"
                f"expected={str(cell['expected']):<6} observed={cell['observed']:<13}{mark}"
            )
        typer.echo(f"summary {record['summary']}")
    if record["summary"]["disagree"]:
        raise typer.Exit(1)
    raise typer.Exit(2 if record["summary"].get("inconclusive", 0) else 0)


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
    config: Path | None = typer.Option(None,"--config"),
) -> None:
    """Export an existing investigation record as a self-contained HTML report."""
    if config:
        from .external.manifest import load_project
        from .external.storage import ProjectStore
        from .external.reporting import html_report
        record=ProjectStore(load_project(config).manifest.duress.project).load("investigations",investigation)["payload"]
        output.write_text(html_report(record))
        typer.echo(str(output.resolve()))
        return
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



def _external_operation(action: str, config: Path, dna: str, *, candidate_config: Path | None = None, json_output: bool = False):
    from .external import artifacts
    from .external.commands import emit, payload
    try:
        document=payload(config,dna)
        if action=="verify":
            result=asyncio.run(artifacts.verify(config,candidate_config,document))
        else:
            result=asyncio.run(getattr(artifacts,action)(config,document))
    except (ValueError,OSError,ImportError) as exc:
        emit({"outcome":"ERROR","reason":str(exc)},json_output)
        raise typer.Exit(3)
    emit(result,json_output)
    codes={"REPRODUCED":1,"DIFFERENT_FAILURE":1,"NOT_REPRODUCED":0,"VERIFIED":0,"NOT_VERIFIED":1,
           "REDUCED":0,"NOT_REDUCED":0,"INCONCLUSIVE":2,"REJECTED":4}
    raise typer.Exit(codes[result["outcome"]])


from .external.commands import install as _install_external_commands
_install_external_commands(app,dna_app)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
