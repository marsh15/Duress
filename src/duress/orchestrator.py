"""Investigation orchestrator (plan C.12, M3).

One investigation: ask the model (live or cached) for source-grounded
hypotheses, check them, run the selected scenarios through the trusted runner,
then characterise every distinct failure into Failure DNA. When the model path
cannot produce a usable hypothesis, the investigation falls back to the real
``EXHAUSTIVE_SET``.

Model text is evidence of nothing. Verdicts come only from executed trials and
the oracle; a hypothesis is ``confirmed`` only when an executed trial violated
the predicted check on the predicted role.

The investigation record is persisted after every step, so a reader can follow
progress from the JSON alone.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import httpx

from . import dna as dna_module
from . import investigator
from . import limits as limits_module
from .llm import LlmCache, LlmClient, LlmConfig, LlmError, ModelBudget
from .models import ScenarioResult, ScenarioSpec, canonical_dict
from .operations import OperationConfig, run_scenario
from .profiles import TargetProfile, load_seed
from .provider_stub import StubServer
from .scenarios import EXHAUSTIVE_SPECS, EXHAUSTIVE_SET
from .store import atomic_write_json, read_json, save_dna

Mode = Literal["live", "cached", "exhaustive"]
MODES: tuple[str, ...] = ("live", "cached", "exhaustive")

# C.14: investigation wall = 130 x min(t_p95, 65 s) + 180 s for model calls.
# t_p95 is an assumption until measured on the host; 2 s is the plan's A4 bound.
ASSUMED_TRIAL_P95_S = 2.0
MODEL_WALL_S = 180.0
MAX_DNA_SIGNATURES = 2
# A cached run without a configured provider keys its fixture with this name, so
# a hand-authored fixture can never be mistaken for a live model's output.
FIXTURE_MODEL = "duress-authored-fixture"


def investigation_wall_s(t_p95: float = ASSUMED_TRIAL_P95_S) -> float:
    return (
        limits_module.INVESTIGATION_TRIAL_CAP
        * min(t_p95, limits_module.TRIAL_WATCHDOG_S)
        + MODEL_WALL_S
    )


def investigations_dir(home: Path) -> Path:
    return home / "investigations"


def investigation_path(home: Path, investigation_id: str) -> Path:
    return investigations_dir(home) / f"{investigation_id}.json"


def new_investigation_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"inv-{stamp}-{uuid.uuid4().hex[:6]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_cache(root: Path, home: Path) -> LlmCache:
    """Committed fixtures first, then responses recorded by earlier live runs."""
    read_dirs = []
    override = os.environ.get("DURESS_LLM_CACHE")
    if override:
        read_dirs.append(Path(override))
    read_dirs += [root / "fixtures" / "llm", home / "llm-cache"]
    return LlmCache(read_dirs, write_dir=home / "llm-cache")


@dataclass
class InvestigationContext:
    profile: TargetProfile
    home: Path
    workdir_root: Path
    stub: StubServer
    root: Path
    limits: limits_module.Limits = field(default_factory=lambda: limits_module.DEFAULT_LIMITS)
    llm_config: LlmConfig | None = None
    llm_transport: httpx.AsyncBaseTransport | None = None
    cache: LlmCache | None = None
    wall_cap_s: float | None = None
    trial_cap: int = limits_module.INVESTIGATION_TRIAL_CAP
    # Optional M4 step run on each new DNA; injected so the orchestrator stays
    # independent of the reducer's internals.
    reducer: Callable[..., Awaitable[dict[str, Any]]] | None = None


class Recorder:
    """Owns the persisted investigation record."""

    def __init__(self, home: Path, record: dict[str, Any]) -> None:
        self.home = home
        self.record = record

    def save(self) -> None:
        self.record["updated_at"] = _now()
        atomic_write_json(
            investigation_path(self.home, self.record["investigation_id"]), self.record
        )

    def event(self, phase: str, message: str, **extra: Any) -> None:
        self.record["phase"] = phase
        self.record["events"].append({"at": _now(), "phase": phase, "message": message, **extra})
        self.save()


def initial_record(
    investigation_id: str, target_id: str, mode: str, *, wall_cap_s: float, trial_cap: int
) -> dict[str, Any]:
    now = _now()
    return {
        "record_type": "investigation",
        "investigation_id": investigation_id,
        "created_at": now,
        "updated_at": now,
        "status": "queued",
        "phase": "queued",
        "target_id": target_id,
        "mode_requested": mode,
        "mode_effective": None,
        "fallback_reason": None,
        "fallback_detail": None,
        "model": None,
        "bundle": None,
        "hypotheses": [],
        "usable_hypotheses": 0,
        "rejected_hypotheses": 0,
        "selected_scenarios": [],
        "scenarios": [],
        "findings": [],
        "dna_ids": [],
        "reductions": [],
        "explanation": "",
        "explanation_source": "template",
        "verdict": None,
        "trials_used": 0,
        "trial_cap": trial_cap,
        "wall_cap_s": wall_cap_s,
        "budget_exhausted": False,
        "events": [],
        "errors": [],
    }


def load_investigation(home: Path, investigation_id: str) -> dict[str, Any]:
    path = investigation_path(home, investigation_id)
    if not path.exists():
        raise FileNotFoundError(investigation_id)
    return read_json(path)


# --------------------------------------------------------------------------- #
# Model phase
# --------------------------------------------------------------------------- #
@dataclass
class ModelPhase:
    hypotheses: list[investigator.Hypothesis] = field(default_factory=list)
    fallback_reason: str | None = None
    fallback_detail: str | None = None


async def _model_phase(
    ctx: InvestigationContext, mode: str, recorder: Recorder
) -> ModelPhase:
    profile = ctx.profile
    seed = load_seed(profile)
    try:
        bundle = investigator.build_source_bundle(profile)
    except investigator.BundleTooLarge as exc:
        return ModelPhase(fallback_reason="bundle_too_large", fallback_detail=str(exc))
    recorder.record["bundle"] = {
        "sha256": bundle.sha256,
        "files": sorted(bundle.files),
        "bytes": bundle.total_bytes,
    }
    messages = investigator.build_messages(profile, bundle, profile.roles)

    config = ctx.llm_config
    model = config.model if config else FIXTURE_MODEL
    budget = ModelBudget()
    client = LlmClient(
        mode=mode,  # type: ignore[arg-type]
        model=model,
        cache=ctx.cache or default_cache(ctx.root, ctx.home),
        budget=budget,
        config=config,
        transport=ctx.llm_transport,
    )
    recorder.record["model"] = {
        "mode": mode,
        "model": model,
        "provider": config.redacted() if config else None,
        "calls": client.calls,
        "budget": budget.snapshot(),
        "response_source": None,
        "provenance": None,
    }
    recorder.event("model", f"requesting hypotheses ({mode})")

    def finish_model_record(response=None) -> None:
        recorder.record["model"]["calls"] = list(client.calls)
        recorder.record["model"]["budget"] = budget.snapshot()
        if response is not None:
            recorder.record["model"]["response_source"] = response.source
            recorder.record["model"]["provenance"] = response.provenance

    try:
        response = await client.complete(messages)
    except LlmError as exc:
        finish_model_record()
        reason = "transport_error" if exc.reason == "not_configured" else exc.reason
        return ModelPhase(fallback_reason=reason, fallback_detail=f"{exc.reason}: {exc.detail}")

    try:
        output = investigator.parse_model_output(response.content)
    except investigator.OutputInvalid as first:
        recorder.event("model", "model output failed validation; one repair attempt")
        try:
            response = await client.complete(
                investigator.repair_messages(messages, response.content, first.errors)
            )
            output = investigator.parse_model_output(response.content)
        except LlmError as exc:
            finish_model_record()
            reason = exc.reason if exc.reason in {"cache_miss", "limit_exceeded"} else "invalid_output"
            return ModelPhase(
                fallback_reason=reason,
                fallback_detail=f"repair call failed: {exc.reason}: {exc.detail}",
            )
        except investigator.OutputInvalid as second:
            finish_model_record(response)
            return ModelPhase(fallback_reason="invalid_output", fallback_detail=second.errors[:2000])
    finish_model_record(response)

    hypotheses = [investigator.assess(draft, bundle, profile.roles, seed) for draft in output.hypotheses]
    if not any(item.usable for item in hypotheses):
        return ModelPhase(
            hypotheses=hypotheses,
            fallback_reason="no_usable_hypotheses",
            fallback_detail=f"{len(hypotheses)} hypotheses, none with valid citations and linkage",
        )
    return ModelPhase(hypotheses=hypotheses)


# --------------------------------------------------------------------------- #
# Execution phase
# --------------------------------------------------------------------------- #
def _scenario_entry(spec: ScenarioSpec, built_id: str, selected_by: str) -> dict[str, Any]:
    return {
        "scenario_id": built_id,
        "template_id": spec.template_id,
        "params": dict(spec.params),
        "selected_by": selected_by,
        "status": "pending",
        "result": None,
        "trials": [],
    }


def _trial_rows(result: ScenarioResult) -> list[dict[str, Any]]:
    rows = []
    for observation, verdict in zip(result.observations, result.verdicts):
        rows.append(
            {
                "trial_id": observation.trial_id,
                "status": verdict.status,
                "reasons": list(verdict.reasons),
                "signature": canonical_dict(verdict.signature) if verdict.signature else None,
                "settlement": observation.settlement,
                "released_by": observation.stub.released_by,
                "duration_s": round(observation.duration_s, 3),
            }
        )
    return rows


def _overall(results: list[ScenarioResult]) -> str:
    if any(item.result.status == "FAIL" for item in results):
        return "FAIL"
    if results and all(item.result.status == "PASS" for item in results):
        return "PASS"
    return "INCONCLUSIVE"


async def run_investigation(
    ctx: InvestigationContext,
    *,
    mode: str,
    investigation_id: str | None = None,
    reduce: bool = False,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    started = time.monotonic()
    wall_cap = ctx.wall_cap_s if ctx.wall_cap_s is not None else investigation_wall_s()
    wall_deadline = started + wall_cap
    investigation_id = investigation_id or new_investigation_id()
    investigations_dir(ctx.home).mkdir(parents=True, exist_ok=True)
    recorder = Recorder(
        ctx.home,
        initial_record(
            investigation_id, ctx.profile.target_id, mode, wall_cap_s=wall_cap, trial_cap=ctx.trial_cap
        ),
    )
    record = recorder.record
    record["status"] = "running"
    recorder.event("start", f"investigating {ctx.profile.target_id} in {mode} mode")

    try:
        await _investigate(ctx, mode, recorder, wall_deadline, reduce)
        record["status"] = "completed"
        recorder.event("done", f"verdict {record['verdict']}")
    except asyncio.CancelledError:
        record["status"] = "failed"
        record["errors"].append("cancelled")
        recorder.event("done", "investigation cancelled")
        raise
    except Exception as exc:  # recorded, never swallowed silently
        record["status"] = "failed"
        record["errors"].append(f"{type(exc).__name__}: {exc}")
        recorder.event("done", "investigation failed")
    record["duration_s"] = round(time.monotonic() - started, 2)
    recorder.save()
    return record


async def _investigate(
    ctx: InvestigationContext,
    mode: str,
    recorder: Recorder,
    wall_deadline: float,
    reduce: bool,
) -> None:
    record = recorder.record
    profile = ctx.profile
    seed = load_seed(profile)

    # 1. Hypotheses (live / cached) or straight to the exhaustive set.
    hypotheses: list[investigator.Hypothesis] = []
    if mode == "exhaustive":
        record["mode_effective"] = "exhaustive"
        specs = list(EXHAUSTIVE_SPECS)
        selected_by = {spec.scenario_id: "exhaustive" for spec in specs}
    else:
        phase = await _model_phase(ctx, mode, recorder)
        hypotheses = phase.hypotheses
        record["hypotheses"] = [item.model_dump(mode="json") for item in hypotheses]
        record["usable_hypotheses"] = sum(1 for item in hypotheses if item.usable)
        record["rejected_hypotheses"] = sum(1 for item in hypotheses if not item.usable)
        if phase.fallback_reason:
            record["mode_effective"] = "exhaustive"
            record["fallback_reason"] = phase.fallback_reason
            record["fallback_detail"] = phase.fallback_detail
            recorder.event(
                "fallback",
                f"falling back to the exhaustive set: {phase.fallback_reason}",
            )
            specs = list(EXHAUSTIVE_SPECS)
            selected_by = {spec.scenario_id: "exhaustive" for spec in specs}
        else:
            record["mode_effective"] = mode
            specs = investigator.select_scenarios(hypotheses)
            selected_by = {}
            for spec in specs:
                selected_by[spec.scenario_id] = (
                    "control" if spec.template_id in {"S1", "S2"} and not spec.params else "hypothesis"
                )
            recorder.event(
                "hypotheses",
                f"{record['usable_hypotheses']} usable of {len(hypotheses)} hypotheses; "
                f"{len(specs)} scenarios selected",
            )

    # 2. Execute every selected scenario through the trusted runner.
    built_list = []
    for spec in specs:
        built = spec.build(profile.roles, seed)
        built_list.append((spec, built))
        record["scenarios"].append(
            _scenario_entry(spec, built.scenario_id, selected_by.get(spec.scenario_id, "exhaustive"))
        )
    record["selected_scenarios"] = [built.scenario_id for _, built in built_list]
    recorder.save()

    explore = limits_module.POLICIES["explore"]
    results: list[ScenarioResult] = []
    for index, (spec, built) in enumerate(built_list):
        entry = record["scenarios"][index]
        if record["trials_used"] + explore.max_attempts > ctx.trial_cap:
            entry["status"] = "skipped"
            entry["skip_reason"] = "trial_cap"
            record["budget_exhausted"] = True
            continue
        entry["status"] = "running"
        recorder.event("explore", f"running {built.scenario_id}", scenario_id=built.scenario_id)
        result = await run_scenario(
            OperationConfig(
                profile=profile,
                scenario=built,
                stub=ctx.stub,
                workdir_root=ctx.workdir_root,
                policy=explore,
                limits=ctx.limits,
                home=ctx.home,
                keep_evidence=True,
            ),
            wall_deadline=wall_deadline,
        )
        results.append(result)
        record["trials_used"] += result.result.trials
        record["budget_exhausted"] = record["budget_exhausted"] or result.result.budget_exhausted
        entry["status"] = "done"
        entry["result"] = result.result.model_dump(mode="json")
        entry["trials"] = _trial_rows(result)
        recorder.event(
            "explore",
            f"{built.scenario_id}: {result.result.status} "
            f"(valid={result.result.valid}, fail={result.result.fail}, trials={result.result.trials})",
            scenario_id=built.scenario_id,
        )

    by_id = {item.scenario_id: item for item in results}

    # 3. Hypothesis outcomes come from execution only.
    if hypotheses:
        resolved = [investigator.resolve_outcome(item, by_id) for item in hypotheses]
        record["hypotheses"] = [item.model_dump(mode="json") for item in resolved]
        hypotheses = resolved
        recorder.save()

    # 4. Characterise each distinct failure signature into DNA.
    counts: dict[str, tuple[int, ScenarioResult]] = {}
    for result in results:
        if result.result.status != "FAIL" or result.result.signature is None:
            continue
        key = result.result.signature.key
        fails = result.result.signature_counts.get(key, result.result.fail)
        if key not in counts or fails > counts[key][0]:
            counts[key] = (fails, result)
    ordered = sorted(counts.items(), key=lambda item: (-item[1][0], item[0]))
    characterize = limits_module.POLICIES["characterize"]
    for key, (_, result) in ordered[:MAX_DNA_SIGNATURES]:
        signature = result.result.signature
        assert signature is not None
        confirming = [
            item.draft.id
            for item in hypotheses
            if item.status.outcome == "confirmed"
            and result.scenario_id in item.scenario_ids
            and investigator.normalise_check(item.draft.predicted_failure.check)
            == investigator.normalise_check(signature.check)
            and item.draft.predicted_failure.kind == signature.kind
            and item.draft.predicted_failure.role in signature.roles
        ]
        finding = {
            "signature": canonical_dict(signature),
            "scenario_id": result.scenario_id,
            "fail": result.result.fail,
            "valid": result.result.valid,
            "hypothesis_ids": confirming,
            "dna_id": None,
            "dna_error": None,
            "explanation": investigator.template_explanation([result]),
        }
        record["findings"].append(finding)
        if record["trials_used"] + characterize.max_attempts > ctx.trial_cap:
            finding["dna_error"] = "trial_cap"
            record["budget_exhausted"] = True
            recorder.save()
            continue
        if wall_deadline - time.monotonic() < ctx.limits.trial_start_margin_s:
            finding["dna_error"] = "budget_exhausted"
            record["budget_exhausted"] = True
            recorder.save()
            continue
        spec, built = next(pair for pair in built_list if pair[1].scenario_id == result.scenario_id)
        recorder.event("characterize", f"characterising {key}", scenario_id=built.scenario_id)
        try:
            document, characterisation = await dna_module.build_dna(
                profile,
                built,
                ctx.stub,
                workdir_root=ctx.workdir_root,
                home=ctx.home,
                limits=ctx.limits,
                provenance={
                    "investigation_id": record["investigation_id"],
                    "hypothesis_id": confirming[0] if confirming else None,
                },
                wall_deadline=wall_deadline,
            )
        except dna_module.DnaError as exc:
            if exc.scenario_result is not None:
                attempted = exc.scenario_result.result
                record["trials_used"] += attempted.trials
                record["budget_exhausted"] = record["budget_exhausted"] or attempted.budget_exhausted
                finding["characterisation"] = attempted.model_dump(mode="json")
            finding["dna_error"] = str(exc)
            record["errors"].append(f"characterise {key}: {exc}")
            recorder.save()
            continue
        record["trials_used"] += characterisation.scenario_result.result.trials
        record["budget_exhausted"] = (
            record["budget_exhausted"] or characterisation.scenario_result.result.budget_exhausted
        )
        if characterisation.signature.key != key:
            finding["dna_error"] = "characterisation_different_signature"
            record["errors"].append(f"characterise {key}: characterisation_different_signature")
            recorder.save()
            continue
        save_dna(ctx.home, document)
        finding["dna_id"] = document.dna_id
        finding["p0"] = characterisation.p0
        finding["characterisation_signature"] = canonical_dict(characterisation.signature)
        record["dna_ids"].append(document.dna_id)
        recorder.event(
            "characterize",
            f"DNA {document.dna_id}: p0={characterisation.p0} "
            f"({characterisation.fail}/{characterisation.valid} valid)",
        )

        # 5. Optional reduction (M4), within the same trial cap.
        if reduce and ctx.reducer is not None:
            remaining = ctx.trial_cap - record["trials_used"]
            if remaining < limits_module.REDUCE_TRIALS + limits_module.BLACK_BOX_TRIALS:
                record["reductions"].append(
                    {"original_dna_id": document.dna_id, "outcome": "SKIPPED", "reason": "trial_cap"}
                )
                continue
            if wall_deadline - time.monotonic() < ctx.limits.trial_start_margin_s:
                record["reductions"].append(
                    {"original_dna_id": document.dna_id, "outcome": "SKIPPED", "reason": "budget_exhausted"}
                )
                continue
            recorder.event("reduce", f"reducing DNA {document.dna_id}")
            summary = await ctx.reducer(
                document.model_dump(mode="json"), wall_deadline=wall_deadline
            )
            record["trials_used"] += int(summary.get("trials", 0))
            record["budget_exhausted"] = record["budget_exhausted"] or bool(summary.get("budget_exhausted"))
            record["reductions"].append(summary)
            recorder.save()

    # 6. Data-only explanation and overall verdict.
    record["explanation"] = investigator.template_explanation(results)
    record["explanation_source"] = "template"
    record["verdict"] = _overall(results)
    record["executed_scenario_ids"] = [item.scenario_id for item in results]
    record["exhaustive_set"] = list(EXHAUSTIVE_SET)
