"""Server-rendered Duress web UI.

The API remains the execution boundary. This package adds page routes, static
assets, and a progress fragment that reads the investigation document written
by the API's workspace.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

_PACKAGE = Path(__file__).resolve().parent
_TEMPLATES = _PACKAGE / "templates"
_STATIC = _PACKAGE / "static"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_ACTIVE = {"queued", "running"}


def _jinja_environment():
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:  # Keep API-only installs importable.
        raise RuntimeError(
            "The Duress web UI requires Jinja2. Add `jinja2>=3.1` to the runtime dependencies."
        ) from exc
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render(name: str, **context: Any) -> str:
    request = context.get("request")
    state = getattr(getattr(request, "app", None), "state", None)
    context.setdefault("ui_hosted", getattr(getattr(state, "settings", None), "hosted", False))
    return _jinja_environment().get_template(name).render(**context)


def _workspace_paths(workspace: Any) -> tuple[Path, Path]:
    home = Path(workspace.home)
    root = Path(getattr(workspace, "root", home.parent))
    return root, home


def _valid_id(value: str) -> bool:
    return bool(_SAFE_ID.fullmatch(value))


def _snapshot_path(workspace: Any, trial_id: str) -> Path | None:
    if not _valid_id(trial_id):
        return None
    root = Path(workspace.home) / "evidence"
    path = root / trial_id / "snapshot" / "app.db"
    # Read only the supervisor's post-settlement copy, never the target's live DB.
    # Refuse symlinks in every component beneath the workspace.
    if any(part.is_symlink() for part in (root, path.parent.parent, path.parent, path)):
        return None
    return path.resolve() if path.is_file() else None


def _evidence_ids(workspace: Any) -> set[str]:
    root = Path(workspace.home) / "evidence"
    return {path.name for path in root.iterdir() if _snapshot_path(workspace, path.name)} if root.is_dir() else set()


def _load_investigation(workspace: Any, investigation_id: str) -> dict[str, Any] | None:
    _, home = _workspace_paths(workspace)
    if not _valid_id(investigation_id):
        return None
    from ..records import load_investigation_record, read_record

    local_path = home / "investigations" / f"{investigation_id}.json"
    try:
        if local_path.exists() or local_path.is_symlink():
            record = read_record(local_path.parent, investigation_id)
        else:
            record = load_investigation_record(workspace, investigation_id)
    except FileNotFoundError:
        return None
    for key in ("hypotheses", "scenarios", "trials", "findings", "events", "dna_ids"):
        if key in record and record[key] is not None and not isinstance(record[key], (list, dict)):
            return None
    if "status" in record and not isinstance(record["status"], str):
        return None
    bundle = record.get("bundle")
    if bundle is not None and (not isinstance(bundle, dict) or
                               ("files" in bundle and not isinstance(bundle["files"], list))):
        return None
    model = record.get("model")
    if model is not None:
        if not isinstance(model, dict):
            return None
        if "calls" in model and not isinstance(model["calls"], list):
            return None
        if any(model.get(key) is not None and not isinstance(model[key], dict)
               for key in ("budget", "provenance")):
            return None
    if str(record.get("record_type", "")).startswith("recorded_"):
        record.setdefault("showcase", True)
    return record


def _load_dna(workspace: Any, dna_id: str) -> dict[str, Any] | None:
    root, home = _workspace_paths(workspace)
    if not _valid_id(dna_id):
        return None
    from ..records import read_record

    local_path = home / "dna" / f"{dna_id}.json"
    try:
        if local_path.exists() or local_path.is_symlink():
            document = read_record(local_path.parent, dna_id)
        else:
            document = read_record(root / "showcase" / "dna", dna_id)
    except FileNotFoundError:
        return None
    if document is not None:
        if not _dna_shape_valid(document):
            return None
        return document
    return None


def _dna_shape_valid(document: Mapping[str, Any]) -> bool:
    """Reject malformed presentation records; execution preflight stays in M2."""
    if not isinstance(document.get("reproducer"), dict) or not all(
        key not in document or isinstance(document[key], dict)
        for key in ("characterization", "provenance_scenario", "provenance", "observations")
    ):
        return False
    reproducer = document["reproducer"]
    schedule = reproducer.get("schedule", {})
    if not isinstance(schedule, dict):
        return False
    if any(key in reproducer and not isinstance(reproducer[key], dict)
           for key in ("failure_signature", "target_contract")):
        return False
    for key in ("deliveries", "steps", "faults"):
        rows = schedule.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return False
    if not isinstance(schedule.get("delivery_policy", {}), dict):
        return False
    return all(isinstance(step.get("deliveries", []), list) for step in schedule.get("steps", []))


def _items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _status(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("status", "state", "outcome", "value"):
            if value.get(key) is not None:
                return _status(value[key])
        return "recorded"
    if isinstance(value, bool):
        return "valid" if value else "invalid"
    return str(value) if value not in (None, "") else "not checked"


def _hypothesis_outcome(item: Mapping[str, Any]) -> str:
    status = item.get("status")
    if isinstance(status, dict) and status.get("outcome") is not None:
        return _status(status.get("outcome"))
    return _status(item.get("outcome"))


def _flatten_trials(document: Mapping[str, Any], scenarios: list[Any]) -> list[dict[str, Any]]:
    recorded = document.get("trials")
    if isinstance(recorded, list) and recorded:
        result = []
        for index, trial in enumerate(recorded, start=1):
            if isinstance(trial, dict):
                result.append({"ordinal": index, **trial})
        if result:
            return result

    result: list[dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        scenario_id = scenario.get("scenario_id") or scenario.get("template_id", "scenario")
        scenario_trials = scenario.get("trials")
        if isinstance(scenario_trials, list):
            for trial in scenario_trials:
                if not isinstance(trial, dict):
                    continue
                result.append(
                    {
                        "ordinal": len(result) + 1,
                        "scenario_id": scenario_id,
                        **trial,
                    }
                )
            if scenario_trials:
                continue
        observations = _items(scenario.get("observations"))
        verdicts = _items(scenario.get("verdicts"))
        for index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                continue
            verdict = verdicts[index] if index < len(verdicts) else {}
            verdict = verdict if isinstance(verdict, dict) else {}
            result.append(
                {
                    "ordinal": len(result) + 1,
                    "trial_id": observation.get("trial_id", f"trial-{index + 1}"),
                    "scenario_id": scenario_id,
                    "status": verdict.get("status", "recorded"),
                    "reasons": verdict.get("reasons", []),
                    "signature": verdict.get("signature"),
                    "duration_s": observation.get("duration_s"),
                    "started_at": observation.get("started_at"),
                }
            )
    return result


def _investigation_view(document: Mapping[str, Any]) -> dict[str, Any]:
    scenarios = _items(document.get("scenarios"))
    hypotheses = []
    for item in _items(document.get("hypotheses")):
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        draft = item.get("draft") if isinstance(item.get("draft"), dict) else item
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        normalized.update(
            {
                "id": draft.get("id", item.get("id")),
                "title": draft.get("title", item.get("title")),
                "mechanism": draft.get("mechanism", item.get("mechanism")),
                "predicted_failure": draft.get("predicted_failure", item.get("predicted_failure")),
                "scenarios": draft.get("scenarios", item.get("scenarios", item.get("scenario_ids", []))),
                "citation_status": _status(status.get("citation", item.get("citation_status"))),
                "structure_status": _status(status.get("structure", item.get("structure_status"))),
                "linkage_status": _status(status.get("linkage", item.get("linkage_status"))),
                "outcome": "rejected" if item.get("usable") is False else _hypothesis_outcome(item),
                "usable": item.get("usable", True),
                "outcome_detail": status.get("outcome_detail", ""),
                "structure_detail": status.get("structure_detail", ""),
            }
        )
        citations = draft.get("citations", item.get("citations", item.get("mechanism_citations", [])))
        normalized["citations"] = [
            {
                **citation,
                "path": citation.get("file", citation.get("path")),
                "start_line": citation.get("line_start", citation.get("start_line")),
                "end_line": citation.get("line_end", citation.get("end_line")),
            }
            for citation in _items(citations)
            if isinstance(citation, dict)
        ]
        if isinstance(status, dict):
            normalized["rejections"] = _items(item.get("rejections")) + _items(
                status.get("citation_errors")
            ) + _items(status.get("linkage_errors"))
        hypotheses.append(normalized)
    confirmed = [item for item in hypotheses if item["outcome"].lower() == "confirmed"]
    trials = _flatten_trials(document, scenarios)
    counts = Counter(_status(item.get("status")).upper() for item in trials)
    scenario_rows = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        result = scenario.get("result") if isinstance(scenario.get("result"), dict) else {}
        scenario_rows.append(
            {
                "template_id": scenario.get("template_id", "Scenario"),
                "scenario_id": scenario.get("scenario_id", ""),
                "params": scenario.get("params", {}),
                "status": result.get("status", "recorded"),
                "lifecycle": _status(scenario.get("status", "recorded")),
                "selected_by": scenario.get("selected_by", "not recorded"),
                "result": result,
                "schedule": scenario.get("schedule") if isinstance(scenario.get("schedule"), dict) else {},
                "control_level": scenario.get("control_level", "not recorded"),
                "valid": result.get("valid", 0),
                "fail": result.get("fail", 0),
                "inconclusive": result.get("inconclusive", 0),
                "trials": result.get(
                    "trials",
                    len(_items(scenario.get("trials"))) or len(_items(scenario.get("observations"))),
                ),
            }
        )
    return {
        "id": document.get("investigation_id", "showcase"),
        "target_id": document.get("target_id", "unknown target"),
        "mode_requested": document.get("mode_requested", "unknown"),
        "mode_effective": document.get("mode_effective", document.get("mode_requested", "unknown")),
        "fallback_reason": document.get("fallback_reason"),
        "fallback_detail": document.get("fallback_detail"),
        "verdict": document.get("verdict"),
        "model": document.get("model") if isinstance(document.get("model"), dict) else {},
        "findings": [item for item in _items(document.get("findings")) if isinstance(item, dict)],
        "selected_scenarios": _items(document.get("selected_scenarios")),
        "trials_used": document.get("trials_used", 0),
        "trial_cap": document.get("trial_cap"),
        "wall_cap_s": document.get("wall_cap_s"),
        "duration_s": document.get("duration_s"),
        "budget_exhausted": document.get("budget_exhausted", False),
        "record": dict(document),
        "status": document.get("status", "unknown"),
        "phase": document.get("phase", ""),
        "created_at": document.get("created_at", ""),
        "showcase": bool(document.get("showcase", False)),
        "hypotheses": hypotheses,
        "confirmed_count": len(confirmed),
        "scenarios": scenario_rows,
        "scenario_count": len(scenario_rows),
        "scenarios_completed": sum(row["lifecycle"] == "done" for row in scenario_rows),
        "scenarios_pending": sum(row["lifecycle"] in {"pending", "queued"} for row in scenario_rows),
        "scenarios_running": sum(row["lifecycle"] == "running" for row in scenario_rows),
        "scenarios_skipped": sum(row["lifecycle"] == "skipped" for row in scenario_rows),
        "scenario_count_label": len(scenario_rows) if scenario_rows else None,
        "trials": trials,
        "trial_count_label": len(trials) if trials else None,
        "trial_counts": {
            "pass": counts.get("PASS", 0),
            "fail": counts.get("FAIL", 0),
            "inconclusive": counts.get("INCONCLUSIVE", 0),
        },
        "events": [item for item in _items(document.get("events")) if isinstance(item, dict)],
        "recorded_summary": document.get("recorded_summary") if isinstance(document.get("recorded_summary"), dict) else None,
        "comparison": document.get("comparison") if isinstance(document.get("comparison"), dict) else None,
        "business_state": document.get("business_state") if isinstance(document.get("business_state"), dict) else None,
        "source_evidence": _items(document.get("source_evidence")),
        "observed_attempts": [item for item in _items(document.get("observed_attempts")) if isinstance(item, dict)],
        "dna_ids": [str(value) for value in _items(document.get("dna_ids")) if value],
        "explanation": document.get("explanation", ""),
        "errors": _items(document.get("errors")) + ([document["error"]] if document.get("error") else []),
    }


def _reduced_document(reduction: Any) -> dict[str, Any] | None:
    if not isinstance(reduction, dict):
        return None
    for key in ("reduced_document", "document", "reduced_dna", "dna", "reduced"):
        candidate = reduction.get(key)
        if isinstance(candidate, dict) and isinstance(candidate.get("reproducer"), dict):
            return candidate
    for key in ("result", "reduction_result"):
        candidate = reduction.get(key)
        if isinstance(candidate, dict):
            nested = _reduced_document(candidate)
            if nested:
                return nested
    return None


def _reduction_view(document: Mapping[str, Any]) -> dict[str, Any]:
    reduction = document.get("reduction")
    reduction = reduction if isinstance(reduction, dict) else None
    reduced = _reduced_document(reduction)
    reduced_schedule = (
        reduced.get("reproducer", {}).get("schedule", {}) if reduced else None
    )
    if reduction and reduced_schedule is None:
        candidate = (
            reduction.get("reduced_schedule")
            or reduction.get("candidate_schedule")
            or reduction.get("schedule")
        )
        reduced_schedule = candidate if isinstance(candidate, dict) else None
    original_schedule = document.get("reproducer", {}).get("schedule", {})
    original_ids = [item.get("id") for item in _items(original_schedule.get("deliveries")) if isinstance(item, dict)]
    reduced_ids = [item.get("id") for item in _items((reduced_schedule or {}).get("deliveries")) if isinstance(item, dict)]
    claim = ""
    if reduction:
        for key in ("claim", "claim_text", "minimality_claim", "statement"):
            if reduction.get(key):
                claim = str(reduction[key])
                break
        if not claim and isinstance(reduction.get("result"), dict):
            claim = str(reduction["result"].get("claim") or "")
    removed = [value for value in original_ids if value not in reduced_ids] if reduced_schedule is not None else []
    return {
        "available": bool(reduction),
        "role": (reduction or {}).get("role"),
        "original_dna_id": (reduction or {}).get("original_dna_id"),
        "claim": claim,
        "reduced_dna_id": (reduced or {}).get("dna_id") or (reduction or {}).get("reduced_dna_id"),
        "reduction_id": (reduction or {}).get("reduction_id"),
        "outcome": (reduction or {}).get("outcome"),
        "primary_dna_id": (reduction or {}).get("primary_dna_id"),
        "reduced_schedule": reduced_schedule,
        "original_delivery_ids": original_ids,
        "reduced_delivery_ids": reduced_ids,
        "removed_delivery_ids": removed,
        "original_delivery_count": len(original_ids),
        "reduced_delivery_count": len(reduced_ids) if reduced_schedule is not None else None,
    }


def _dna_view(document: Mapping[str, Any], *, showcase: bool = False) -> dict[str, Any]:
    reproducer = document.get("reproducer") if isinstance(document.get("reproducer"), dict) else {}
    schedule = reproducer.get("schedule") if isinstance(reproducer.get("schedule"), dict) else {}
    characterization = document.get("characterization") if isinstance(document.get("characterization"), dict) else {}
    observations = document.get("observations") if isinstance(document.get("observations"), dict) else {}
    signature = reproducer.get("failure_signature") if isinstance(reproducer.get("failure_signature"), dict) else {}
    deliveries = [item for item in _items(schedule.get("deliveries")) if isinstance(item, dict)]
    intents = Counter(str(item.get("effect_intent", "unknown")) for item in deliveries)
    return {
        "id": document.get("dna_id", "unknown"),
        "document": document,
        "reproducer": reproducer,
        "schedule": schedule,
        "deliveries": deliveries,
        "intent_counts": dict(intents),
        "signature": signature,
        "characterization": characterization,
        "observations": observations,
        "release_condition": reproducer.get("release_condition", "not recorded"),
        "release_conditions": characterization.get("release_conditions", {}),
        "violation_rows": _items(observations.get("violation_rows")),
        "reduction": _reduction_view(document),
        "provenance": document.get("provenance", {}),
        "derived_from": (document.get("provenance") or {}).get("reduced_from"),
        "target_id": (document.get("provenance") or {}).get("target_id", "razorpay-vulnerable"),
        "showcase": showcase,
    }


def _showcase_context(workspace: Any) -> dict[str, Any] | None:
    from ..records import read_record

    root, _ = _workspace_paths(workspace)
    showcase_dir = root / "showcase"
    try:
        investigation = read_record(showcase_dir, "investigation")
    except FileNotFoundError:
        return None
    dna_ids = [str(value) for value in _items(investigation.get("dna_ids")) if value]
    dna_dir = showcase_dir / "dna"
    if dna_dir.is_dir():
        known = {path.stem for path in dna_dir.glob("*.json")}
        dna_ids = list(dict.fromkeys(dna_ids + sorted(known)))
    return {
        "investigation_id": investigation.get("investigation_id") or "showcase",
        "target_id": investigation.get("target_id", "unknown target"),
        "mode_effective": investigation.get("mode_effective", "not recorded"),
        "status": investigation.get("status", "recorded"),
        "confirmed": sum(
            1
            for item in _items(investigation.get("hypotheses"))
            if isinstance(item, dict) and _hypothesis_outcome(item).lower() == "confirmed"
        ) if _items(investigation.get("hypotheses")) else None,
        "comparison": investigation.get("comparison") if isinstance(investigation.get("comparison"), dict) else None,
        "dna_ids": dna_ids,
    }


def _investigation_records(workspace: Any) -> list[dict[str, Any]]:
    from ..records import investigation_index

    return investigation_index(workspace)


def _target_views(workspace: Any) -> list[dict[str, Any]]:
    registry = getattr(workspace, "registry", None)
    if registry is None:
        return []
    return [
        {
            "target_id": profile.target_id,
            "profile": profile.profile_id,
            "variant": profile.variant,
            "domain": profile.domain,
            "contract_version": profile.contract_version,
            "workers": profile.workers,
        }
        for profile in sorted(registry.all(),
                              key=lambda item: (item.target_id.startswith("fixture-"), item.target_id))
    ]


def _dna_records(workspace: Any) -> list[dict[str, Any]]:
    from ..records import read_record

    root, home = _workspace_paths(workspace)
    items = []
    home_paths = {path.stem: path for path in (home / "dna").glob("*.json")}
    showcase_paths = {path.stem: path for path in (root / "showcase" / "dna").glob("*.json")}
    for dna_id in sorted(set(home_paths) | set(showcase_paths)):
        path = home_paths.get(dna_id) or showcase_paths[dna_id]
        try:
            document = read_record(path.parent, dna_id)
        except FileNotFoundError:
            continue
        if not _dna_shape_valid(document):
            continue
        reproducer = document.get("reproducer") if isinstance(document.get("reproducer"), dict) else {}
        items.append(
            {
                "dna_id": document.get("dna_id"),
                "signature": reproducer.get("failure_signature"),
                "control_level": reproducer.get("control_level"),
                "p0": (document.get("characterization") or {}).get("p0"),
                "template_id": (document.get("provenance_scenario") or {}).get("template_id"),
                "created_at": (document.get("provenance") or {}).get("created_at"),
                "reduction": document.get("reduction"),
            }
        )
    return items


def _load_operation(workspace: Any, operation_id: str) -> dict[str, Any] | None:
    if not _valid_id(operation_id):
        return None
    from ..records import load_operation_record

    try:
        return load_operation_record(workspace, operation_id)
    except FileNotFoundError:
        return None


def _operation_view(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    nested = payload.get("outcome")
    outcome = nested if isinstance(nested, dict) else payload
    return {
        "id": record.get("operation_id", "unknown"),
        "kind": record.get("kind", "operation"),
        "created_at": record.get("created_at", ""),
        "dna_id": record.get("dna_id") or outcome.get("dna_id") or payload.get("dna_id"),
        "target_id": record.get("target_id") or outcome.get("target_id") or outcome.get("candidate_target_id") or "",
        "outcome": outcome.get("outcome", "recorded"),
        "reason": outcome.get("reason"),
        "valid": outcome.get("valid"),
        "fail": outcome.get("fail"),
        "inconclusive": outcome.get("inconclusive"),
        "trials": outcome.get("trials"),
        "control": outcome.get("control") if isinstance(outcome.get("control"), dict) else None,
        "candidate": outcome.get("candidate") if isinstance(outcome.get("candidate"), dict) else None,
        "statement": outcome.get("statement", ""),
        "payload": record.get("payload", {}),
    }


def _not_found_page(request: Request, resource: str) -> HTMLResponse:
    return HTMLResponse(
        _render("not_found.html", request=request, resource=resource),
        status_code=404,
    )


def install_web(app: FastAPI, workspace: Callable[[], Any]) -> FastAPI:
    """Mount the Duress pages and local static assets on an existing API app.

    ``workspace`` must be the same zero-argument callable used by the API so
    pages read documents from the active workspace without creating a second
    execution path.
    """

    router = APIRouter()
    app.mount("/web/static", StaticFiles(directory=str(_STATIC)), name="duress-web-static")

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(request: Request) -> Response:
        store = workspace()
        recent_findings = []
        for record in _investigation_records(store)[:8]:
            for finding in _investigation_view(record)["findings"][:3]:
                recent_findings.append({**finding,
                    "investigation_id": record["investigation_id"],
                    "target_id": record.get("target_id", "Target not recorded")})
        return HTMLResponse(_render("index.html", request=request,
                                    showcase=_showcase_context(store),
                                    recent_findings=recent_findings))

    @router.get("/design-system", response_class=HTMLResponse, include_in_schema=False)
    async def design_system_page(request: Request) -> Response:
        return HTMLResponse(_render("design_system.html", request=request))

    @router.get("/guide", response_class=HTMLResponse, include_in_schema=False)
    async def guide_page(request: Request) -> Response:
        return HTMLResponse(_render("guide.html", request=request,
                                    hosted=getattr(getattr(request.app.state, "settings", None), "hosted", False)))

    @router.get("/targets", response_class=HTMLResponse, include_in_schema=False)
    async def targets_page(request: Request) -> Response:
        return HTMLResponse(
            _render("targets.html", request=request, targets=_target_views(workspace()))
        )

    @router.get("/investigations", response_class=HTMLResponse, include_in_schema=False)
    async def investigation_history(request: Request) -> Response:
        from ..llm import LlmConfig

        store = workspace()
        return HTMLResponse(
            _render(
                "investigations.html",
                request=request,
                records=_investigation_records(store),
                capabilities={"live_available": LlmConfig.from_env() is not None,
                              "targets": _target_views(store)},
            )
        )

    @router.get("/investigations/{investigation_id}", response_class=HTMLResponse, include_in_schema=False)
    async def investigation_page(investigation_id: str, request: Request) -> Response:
        document = _load_investigation(workspace(), investigation_id)
        if document is None:
            return _not_found_page(request, "investigation")
        return HTMLResponse(
            _render(
                "investigation.html",
                request=request,
                view=_investigation_view(document),
                evidence_ids=_evidence_ids(workspace()),
                poll=str(document.get("status", "unknown")).lower() in _ACTIVE,
            )
        )

    @router.get("/investigations/{investigation_id}/progress", response_class=HTMLResponse, include_in_schema=False)
    async def investigation_progress(investigation_id: str, request: Request) -> Response:
        document = _load_investigation(workspace(), investigation_id)
        if document is None:
            return _not_found_page(request, "investigation")
        view = _investigation_view(document)
        return HTMLResponse(
            _render(
                "_investigation_live.html",
                request=request,
                view=view,
                evidence_ids=_evidence_ids(workspace()),
                poll_url=f"/investigations/{investigation_id}/progress",
                poll=str(view["status"]).lower() in _ACTIVE,
            )
        )

    @router.get("/reductions/{reduction_id}", response_class=HTMLResponse, include_in_schema=False)
    async def reduction_page(reduction_id: str, request: Request) -> Response:
        from ..records import read_record

        store = workspace()
        try:
            record = read_record(store.home / "reductions", reduction_id)
        except FileNotFoundError:
            return _not_found_page(request, "reduction")
        original = _load_dna(store, str(record.get("original_dna_id", "")))
        return HTMLResponse(_render("reduction.html", request=request, record=record,
                                    original=original or {},
                                    poll=record.get("status") in {"queued", "running"}))

    @router.get("/differential", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/differential/{differential_id}", response_class=HTMLResponse, include_in_schema=False)
    async def differential_page(request: Request, differential_id: str | None = None) -> Response:
        from ..records import read_record

        store = workspace()
        record = None
        if differential_id is not None:
            try:
                record = read_record(store.home / "differentials", differential_id)
            except FileNotFoundError:
                return _not_found_page(request, "differential comparison")
        history = []
        for path in (store.home / "differentials").glob("*.json"):
            try:
                history.append(read_record(path.parent, path.stem))
            except FileNotFoundError:
                continue
        history.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return HTMLResponse(_render("differential.html", request=request, record=record,
                                    records=history, targets=_target_views(store),
                                    poll=bool(record and record.get("status") in {"queued", "running"})))

    @router.get("/dna/{dna_id}", response_class=HTMLResponse, include_in_schema=False)
    async def dna_page(dna_id: str, request: Request) -> Response:
        store = workspace()
        document = _load_dna(store, dna_id)
        if document is None:
            return _not_found_page(request, "Failure DNA")
        from ..records import read_record
        try:
            bundled = read_record(Path(store.root) / "showcase" / "dna", dna_id)
        except FileNotFoundError:
            bundled = None
        return HTMLResponse(_render("dna.html", request=request,
                                    view=_dna_view(document, showcase=document == bundled),
                                    evidence_ids=_evidence_ids(store)))

    @router.get("/evidence/{trial_id}", response_class=HTMLResponse, include_in_schema=False)
    def evidence_page(trial_id: str, request: Request) -> Response:
        import sqlite3

        store = workspace()
        path = _snapshot_path(store, trial_id)
        if path is None:
            return _not_found_page(request, "retained trial snapshot")
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1) as connection:
                connection.row_factory = sqlite3.Row
                tables = {name: [dict(row) for row in connection.execute(
                    f"SELECT * FROM {name} ORDER BY rowid LIMIT 1000"
                )] for name in ("orders", "payments", "fulfilments")}
        except (sqlite3.Error, OSError):
            return _not_found_page(request, "readable retained trial snapshot")
        return HTMLResponse(_render("evidence.html", request=request,
                                    trial_id=trial_id, tables=tables))

    @router.get("/dna", response_class=HTMLResponse, include_in_schema=False)
    async def dna_library(request: Request) -> Response:
        return HTMLResponse(
            _render("dna_library.html", request=request, items=_dna_records(workspace()))
        )

    @router.get("/operations/{operation_id}", response_class=HTMLResponse, include_in_schema=False)
    async def operation_page(operation_id: str, request: Request) -> Response:
        record = _load_operation(workspace(), operation_id)
        if record is None:
            return _not_found_page(request, "operation")
        return HTMLResponse(
            _render("operation.html", request=request, view=_operation_view(record))
        )

    @router.get("/dna/{dna_id}/download", include_in_schema=False)
    async def dna_download(dna_id: str) -> Response:
        if not _valid_id(dna_id):
            raise HTTPException(status_code=404, detail="unknown_dna")
        root, home = _workspace_paths(workspace())
        from ..records import read_record

        path = home / "dna" / f"{dna_id}.json"
        if path.exists() or path.is_symlink():
            try:
                read_record(path.parent, dna_id)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="unknown_dna") from None
            return FileResponse(path, media_type="application/json", filename=f"{dna_id}.json")
        showcase_path = root / "showcase" / "dna" / f"{dna_id}.json"
        try:
            read_record(showcase_path.parent, dna_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown_dna") from None
        return FileResponse(showcase_path, media_type="application/json", filename=f"{dna_id}.json")

    app.include_router(router)
    return app


def _report_css() -> str:
    css = "\n".join((_STATIC / name).read_text(encoding="utf-8")
                    for name in ("tokens.css", "site.css", "workspace.css"))
    # Reports remain portable without a running server or external font requests.
    import base64
    for font in ("Geist", "GeistMono"):
        encoded = base64.b64encode((_STATIC / "fonts" / f"{font}.woff2").read_bytes()).decode("ascii")
        css = css.replace(f"fonts/{font}.woff2", f"data:font/woff2;base64,{encoded}")
    license_text = (_STATIC / "fonts" / "LICENSE.txt").read_text(encoding="utf-8")
    return "/* Embedded Geist font license\n" + license_text + "\n*/\n" + css


def render_report(document: Mapping[str, Any] | Any) -> str:
    """Render an investigation document as a self-contained static HTML report.

    The helper accepts either a raw dictionary or a Pydantic-style object with
    ``model_dump(mode="json")`` and is intended for the CLI's report command.
    """

    if hasattr(document, "model_dump"):
        document = document.model_dump(mode="json")
    if not isinstance(document, Mapping):
        raise TypeError("investigation report expects a mapping or model_dump object")
    css = _report_css()
    return _render(
        "report.html",
        view=_investigation_view(document),
        css=css,
    )


render_investigation_report = render_report

__all__ = ["install_web", "render_report", "render_investigation_report"]
