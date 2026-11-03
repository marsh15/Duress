"""Shared local-first external investigation service for CLI and approved UI."""
from __future__ import annotations

import asyncio
import copy
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..aggregation import aggregate
from ..limits import POLICIES, Policy
from ..models import FailureSignature, TrialVerdict, Violation, canonical_sha256
from .contract import Snapshot, evaluate
from .lifecycle import ExternalSupervisor
from .manifest import LoadedProject, Scenario, load_project


def assert_local():
    if os.environ.get("DURESS_HOSTED", "").lower() in {"1", "true", "yes"}:
        raise ValueError("external execution is disabled in the hosted demonstration")


def redact(project: LoadedProject, value: Any, runtime_secret: str | None = None) -> Any:
    from .storage import _redact
    value = _redact(value)
    refs = [*project.manifest.target.env_refs.values(),
            *project.manifest.delivery.header_refs.values(),
            project.manifest.delivery.secret_ref, project.manifest.observation.database_ref,
            project.manifest.observation.observer_ref, project.manifest.target.proxy_ref]
    secrets = [os.environ[r] for r in refs if r and os.environ.get(r)]
    if runtime_secret:
        secrets.append(runtime_secret)
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {k: redact(project, v, runtime_secret) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(project, v, runtime_secret) for v in value]
    return value


def precheck(project: LoadedProject):
    assert_local()
    manifest = project.manifest
    for ref in [*manifest.target.env_refs.values(), *manifest.delivery.header_refs.values()]:
        value = project.resolve(ref)
        if ref in manifest.delivery.header_refs.values() and any(c in value for c in "\r\n"):
            raise ValueError("resolved HTTP header contains a newline")
    if manifest.observation.adapter == "postgresql@1":
        project.resolve(manifest.observation.database_ref)
        project.resolve(manifest.observation.observer_ref or manifest.observation.database_ref)
    if manifest.target.mode == "staging":
        from . import network
        # Opt-in proxy/CA settings are checked before any delivery is attempted.
        network.validate(project)
    if manifest.target.mode == "staging" and manifest.delivery.signature != "none":
        project.resolve(manifest.delivery.secret_ref)


async def trial(project: LoadedProject, scenario: Scenario, home: Path) -> dict[str, Any]:
    precheck(project)
    supervisor = (StagingSupervisor if project.manifest.target.mode == "staging" else ExternalSupervisor)(project, scenario, home)
    observation = await supervisor.run()
    missing = Snapshot(entities={}, available=False, valid=False, settled=False, error="state_unavailable")
    before, after = supervisor.before or missing, supervisor.after or missing
    context = copy.deepcopy(scenario.context)
    declarations = {name:intent.model_dump(mode="json") for name,intent in scenario.acknowledgements.items()}
    legacy_ack = context.pop("acknowledgement",None)
    if legacy_ack is not None:
        declarations[scenario.ack_delivery_id or scenario.deliveries[0].id] = legacy_ack
    results = evaluate(project.contract, before, after, context=context)
    if declarations:
        results = [r for r in results if not any(i.id==r.id and i.kind=="acknowledgement_consistency" for i in project.contract.invariants)]
        for delivery_id, declaration in declarations.items():
            # A receipt from one delivery never supplies another's evidence.
            # Each actual response is checked, including responses before retries.
            responses = [a for a in observation.attempts if a.delivery_id==delivery_id and a.status_code]
            for response in responses or [None]:
                ack = {**declaration,"successful":bool(response and response.is_2xx),
                       "status_code":response.status_code if response else None}
                evaluated = evaluate(project.contract,before,after,context={**context,"acknowledgement":ack})
                results.extend(r for r in evaluated if any(i.id==r.id and i.kind=="acknowledgement_consistency" for i in project.contract.invariants))
    violations = [Violation(check=f"{r.id}@{r.version}", kind=r.reason,
                  entity_ids=[str(v) for identity in r.identities for v in identity.values()],
                  independent=True) for r in results if r.status == "violated"]
    reasons = list(observation.harness_errors)
    if observation.watchdog_fired:
        reasons.append("watchdog")
    if not observation.target_ready or not observation.instance_ok:
        reasons.append("target_not_ready")
    if observation.execution_deadline_exceeded or observation.cancelled_attempts:
        reasons.append("execution_incomplete")
    # Staging never reports a process drain. Only a positively established remote
    # quiescence replaces that disclosure; the default stays "drain_unverified".
    quiescence_established = getattr(supervisor, "quiescence_established", False)
    if observation.settlement != "clean" and not quiescence_established:
        reasons.append("settlement:" + observation.settlement)
    if any(r.status in {"not_evaluable", "invalid"} for r in results):
        reasons.extend(sorted({r.reason for r in results if r.status in {"not_evaluable", "invalid"}}))
    delivered = {a.delivery_id for a in observation.attempts if a.outcome in {"response", "timeout"}}
    if delivered != {d.id for d in scenario.deliveries}:
        reasons.append("deliveries_incomplete")
    if scenario.require_overlap:
        for step in scenario.steps:
            if len(step.deliveries) > 1:
                attempts = [next((a for a in observation.attempts if a.delivery_id == name), None)
                            for name in step.deliveries]
                if any(a is None for a in attempts) or max(a.started_s for a in attempts) >= min(a.ended_s for a in attempts):
                    reasons.append("concurrency_overlap_not_observed")
    if scenario.require_retry and not any(a.attempt > 1 for a in observation.attempts):
        reasons.append("retry_not_observed")
    pass_failures = []
    for delivery_id, status in scenario.expected_status.items():
        attempts = [a for a in observation.attempts if a.delivery_id == delivery_id and a.status_code]
        if not attempts:
            reasons.append("response_missing:" + delivery_id)
        elif attempts[-1].status_code // 100 != int(status[0]):
            pass_failures.append("unexpected_response:" + delivery_id)
    attested = ["remote_quiescence_attested"] if quiescence_established else []
    if reasons:
        verdict = TrialVerdict(status="INCONCLUSIVE", reasons=sorted(set(reasons)), violations=violations)
    elif violations or pass_failures:
        signature = (FailureSignature.from_violation(violations[0]) if violations else
                     FailureSignature(check="http-response@1", kind=pass_failures[0], roles=[]))
        verdict = TrialVerdict(status="FAIL", signature=signature, violations=violations,
                               reasons=[v.check for v in violations] + pass_failures + attested)
    else:
        verdict = TrialVerdict(status="PASS", reasons=["all_declared_invariants_satisfied", *attested])
    execution = observation.model_dump(mode="json", exclude={"snapshot", "workdir", "stub"})
    for attempt in execution["attempts"]:
        attempt["body_status"] = attempt["body_scope"] = None
        if attempt["error"]:
            attempt["error"] = attempt["outcome"]
    record = {"schema_version":1, "trial_id":observation.trial_id,
        "scenario_id":scenario.id, "before":before.model_dump(mode="json"),
        "after":after.model_dump(mode="json"), "execution":execution,
        "invariants":[r.model_dump(mode="json") for r in results], "verdict":verdict.model_dump(mode="json")}
    evidence = getattr(supervisor, "quiescence_evidence", None)
    if evidence is not None:
        record["quiescence"] = evidence
    return redact(project, record, supervisor.secret)


class StagingSupervisor(ExternalSupervisor):
    """Remote delivery with no local process. Quiescence is established only by the probe.

    Without a configured ``target.quiescence`` the trial still delivers and
    observes, but always reports INCONCLUSIVE. With one, the baseline is read
    only after quiescence holds, and a failed pre-probe sends no events at all.
    """

    def __init__(self, project: LoadedProject, scenario: Scenario, home: Path):
        super().__init__(project, scenario, home)
        self.quiescence_established = False
        self.quiescence_evidence: dict[str, Any] | None = None

    @property
    def base_url(self):
        return self.project.manifest.target.base_url.rstrip("/")

    def _probe_headers(self) -> dict[str, str]:
        spec = self.project.manifest.delivery
        return {**spec.headers, **{name: self.project.resolve(ref) for name, ref in spec.header_refs.items()}}

    async def run(self):
        from ..limits import DEFAULT_LIMITS
        from ..runner import run_schedule
        from . import network
        from .observers import observe
        from .quiescence import await_quiescence
        manifest = self.project.manifest
        self.schema = manifest.observation.schema_name
        self.secret = (self.project.resolve(manifest.delivery.secret_ref)
                       if manifest.delivery.signature != "none" else "")

        def reader(settled: bool):
            return lambda: observe(self.project.contract, kind="postgresql",
                                   database=self.observer_database, schema=self.schema, settled=settled)

        probe = manifest.target.quiescence
        before_probe = after_probe = None
        try:
            self.observation.settlement = "drain_unverified"
            if probe is None:
                self.before = await asyncio.to_thread(reader(False))
                deliver = True
            else:
                before_probe = await await_quiescence(self.project, reader(True), self._probe_headers())
                deliver = before_probe.established
                self.before = before_probe.snapshot if deliver else None
                if not deliver:
                    # An unsettled baseline cannot be measured, so no event is sent.
                    self.observation.harness_errors.append(before_probe.reason)
            if deliver:
                self.execution = await run_schedule(self.scenario, base_url=self.base_url,
                    secret=self.secret, roles={}, limits=DEFAULT_LIMITS, request_builder=self.request,
                    transport=network.transport(self.project))
                self.observation.attempts = self.execution.attempts
                self.observation.execution_deadline_exceeded = self.execution.execution_deadline_exceeded
                self.observation.cancelled_attempts = self.execution.cancelled_attempts
                self.observation.undispatched = self.execution.undispatched
                if self.execution.error:
                    self.observation.harness_errors.append("runner_error")
            if probe is None:
                self.after = await asyncio.to_thread(reader(False))
                self.observation.harness_errors.append("staging_quiescence_unverified")
            elif deliver:
                after_probe = await await_quiescence(self.project, reader(True), self._probe_headers())
                if after_probe.established:
                    self.after = after_probe.snapshot
                else:
                    self.observation.harness_errors.append(after_probe.reason)
                    self.after = await asyncio.to_thread(reader(False))
            # A 2xx delivery response is never an input here: only the attestation is.
            self.quiescence_established = bool(
                before_probe and after_probe and before_probe.established and after_probe.established)
            if probe is not None:
                self.quiescence_evidence = {"before": before_probe.evidence,
                                            **({"after": after_probe.evidence} if after_probe else {})}
            return self.observation
        finally:
            shutil.rmtree(self.workdir, ignore_errors=True)


async def run_series(project: LoadedProject, scenario: Scenario, home: Path,
                     policy: Policy = POLICIES["explore"]) -> dict[str, Any]:
    trials = []
    verdicts = []
    started = time.monotonic()
    for _ in range(policy.max_attempts):
        if time.monotonic() - started > policy.max_attempts * 65:
            break
        item = await trial(project, scenario, home)
        trials.append(item)
        verdicts.append(TrialVerdict.model_validate(item["verdict"]))
        if "replay_schema_incompatible" in verdicts[-1].reasons:
            break
        if sum(v.status != "INCONCLUSIVE" for v in verdicts) >= policy.required_valid:
            break
    result = aggregate(verdicts, policy)
    return {"scenario_id":scenario.id,"schedule":scenario.model_dump(mode="json"),
            "result":result.model_dump(mode="json"), "trials":trials}


async def investigate(config: Path, *, scenario_id: str | None = None,
                      policy: str = "explore", home: Path | None = None) -> dict[str, Any]:
    from .artifacts import build_dna
    from .storage import ProjectStore
    project = load_project(config)
    precheck(project)
    if policy not in {"explore", "characterize"}:
        raise ValueError("external investigations support explore or characterize policies")
    store = ProjectStore(project.manifest.duress.project, home=home)
    run_id = "i" + uuid.uuid4().hex[:16]
    record = {"schema_version":1,"investigation_id":run_id,"project":project.manifest.duress.project,
              "target_id":project.manifest.target.id,"adapter":"external@1",
              "contract_id":project.contract.id,"contract_version":project.contract.version,
              "status":"running","scenarios":[],"dna":[],"verdict":"INCONCLUSIVE"}
    store.save("investigations", record, run_id)
    scenarios = [project.scenario(scenario_id)] if scenario_id else project.manifest.scenarios
    if len(scenarios) * POLICIES[policy].max_attempts > 130:
        raise ValueError("investigation exceeds the 130-trial cap")
    try:
        for scenario in scenarios:
            result = await run_series(project, scenario, store.home, POLICIES[policy])
            record["scenarios"].append(result)
            if result["result"]["status"] == "FAIL":
                dna = build_dna(project, scenario, result)
                store.save("dna", dna, dna["dna_id"])
                record["dna"].append(dna["dna_id"])
            store.save("investigations", record, run_id)
        statuses = {s["result"]["status"] for s in record["scenarios"]}
        record["verdict"] = "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
        record["status"] = "completed"
    except BaseException:
        record["status"] = "interrupted"
        raise
    finally:
        store.save("investigations", record, run_id)
    return record


async def doctor(config: Path) -> dict[str, Any]:
    """Read-only: never initialize, start a process, or deliver an event."""
    project = load_project(config)
    precheck(project)
    spec = project.manifest.observation
    result = {"project":project.manifest.duress.project,"target_id":project.manifest.target.id,
              "configuration":"valid","mode":project.manifest.target.mode,
              "contract":f"{project.contract.id}@{project.contract.version}",
              "scenarios":[s.id for s in project.manifest.scenarios],
              "business_mutations":False,"readiness":"checked during isolated trial",
              "observer":"validated during isolated initialization"}
    if spec.adapter == "postgresql@1":
        import psycopg
        try:
            with psycopg.connect(project.resolve(spec.observer_ref or spec.database_ref), connect_timeout=3) as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SELECT 1").fetchone()
            result["observer"] = "PostgreSQL read-only connection available; tables checked per trial"
        except psycopg.Error:
            result["observer"] = "unavailable"
            result["configuration"] = "inconclusive"
    return result
