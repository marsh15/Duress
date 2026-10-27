"""Onboarding and operations on trusted local project manifests."""
import asyncio
import json
from pathlib import Path

import typer

from .manifest import load_project
from .storage import ProjectStore, register_project


def emit(value, json_output=True):
    if json_output:
        typer.echo(json.dumps(value,indent=2))
    elif isinstance(value,dict):
        typer.echo(f"{value.get('project','Duress')}  {value.get('outcome',value.get('verdict',value.get('configuration','')))}")
        for key in ("target_id","contract","mode","adapter","observer","readiness"):
            if key in value:
                typer.echo(f"{key:<12} {value[key]}")
        if value.get("reason"):
            typer.echo(value["reason"])
        for item in value.get("scenarios",[]):
            if isinstance(item,dict):
                typer.echo(f"  {item['scenario_id']}: {item['result']['status']}")
                for trial in item.get("trials",[])[:1]:
                    for result in trial["invariants"]:
                        if result["status"] != "satisfied":
                            typer.echo(f"    {result['id']}: {result['reason']} {result['identities']}")
            else:
                typer.echo(f"  scenario: {item}")
        if value.get("dna"):
            typer.echo("Failure DNA: " + ", ".join(value["dna"]))
    else:
        typer.echo(json.dumps(value,indent=2))


def payload(config: Path, identifier: str):
    if Path(identifier).is_file():
        path=Path(identifier)
        if path.stat().st_size>2_000_000:
            raise ValueError("artifact exceeds 2MB")
        return json.loads(path.read_text())
    project=load_project(config)
    return ProjectStore(project.manifest.duress.project).load("dna",identifier)["payload"]


def install(app: typer.Typer, dna_app: typer.Typer):
    @app.command("init")
    def init(directory: Path = typer.Argument(Path(".")), project: str = typer.Option("my-integration","--project")):
        """Create a runnable local starter; never overwrite existing files."""
        from .onboarding import initialize
        try:
            path=initialize(directory,project)
        except (ValueError,OSError) as exc:
            typer.echo(f"error: {exc}",err=True)
            raise typer.Exit(3)
        typer.echo(f"Created {path}. Next: duress doctor --config {path}")

    @app.command("doctor")
    def doctor(config: Path = typer.Option(Path("duress.toml"),"--config"), json_output: bool = typer.Option(False,"--json")):
        """Validate configuration and read-only connectivity; makes no business changes."""
        from .services import doctor as diagnose
        try:
            result=asyncio.run(diagnose(config))
        except (ValueError,OSError,ImportError) as exc:
            emit({"configuration":"invalid","reason":str(exc)},json_output)
            raise typer.Exit(3)
        emit(result,json_output)
        raise typer.Exit(0 if result["configuration"]=="valid" else 2)

    @app.command("register")
    def register(config: Path = typer.Option(Path("duress.toml"),"--config"),json_output: bool = typer.Option(False,"--json")):
        """Register a trusted local manifest for the local workbench."""
        from .services import assert_local
        try:
            assert_local()
            load_project(config)
            result=register_project(config)
        except (ValueError,OSError) as exc:
            typer.echo(f"error: {exc}",err=True)
            raise typer.Exit(3)
        emit(result,json_output)

    @app.command("check")
    def check(config: Path=typer.Option(Path("duress.toml"),"--config"),
              scenario: str|None=typer.Option(None,"--scenario"),
              output: Path|None=typer.Option(None,"--output"),
              junit_path: Path|None=typer.Option(None,"--junit"),
              artifacts: Path|None=typer.Option(None,"--artifacts"),
              json_output: bool=typer.Option(False,"--json")):
        """CI gate: 0 PASS, 1 FAIL, 2 INCONCLUSIVE, 3 configuration/operational error."""
        from .services import investigate
        from .reporting import export_run
        try:
            result=asyncio.run(investigate(config,scenario_id=scenario))
            export_run(result,output=output,junit_path=junit_path,artifacts=artifacts,config=config)
        except (ValueError,OSError,ImportError) as exc:
            emit({"outcome":"ERROR","reason":str(exc)},json_output)
            raise typer.Exit(3)
        emit(result,json_output)
        raise typer.Exit({"PASS":0,"FAIL":1,"INCONCLUSIVE":2}[result["verdict"]])

    @dna_app.command("export")
    def dna_export(dna: str, config: Path=typer.Option(...,"--config"), output: Path=typer.Option(...,"--output")):
        """Export an exact external Failure DNA artifact without execution."""
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(payload(config,dna),indent=2)+"\n")
        typer.echo(str(output))

    @app.command("project-store")
    def project_store(config:Path=typer.Option(...,"--config"),
                      action:str=typer.Option("diagnostics","--action"),
                      destination:Path|None=typer.Option(None,"--destination"),
                      source:Path|None=typer.Option(None,"--source"),
                      retain_days:int=typer.Option(30,"--retain-days",min=1),
                      apply:bool=typer.Option(False,"--apply")):
        """Diagnose/reconcile records, dry-run retention, and integrity-checked export/import."""
        store=ProjectStore(load_project(config).manifest.duress.project)
        if action=="diagnostics": result=store.diagnostics()
        elif action=="reconcile": result=store.reconcile()
        elif action=="cleanup": result=store.cleanup(retain_days,dry_run=not apply)
        elif action=="export" and destination: result={"path":str(store.export_to(destination))}
        elif action=="import" and destination and source: result=store.import_from(source,destination)
        else: raise typer.BadParameter("action is diagnostics/reconcile/cleanup/export/import; export needs destination, import also source")
        emit(result)
