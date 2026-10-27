"""external-dna@1 integrity and semantic compatibility; no executable artifacts."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ..limits import POLICIES
from ..models import FailureSignature, StrictModel, canonical_sha256
from ..verify import candidate_size
from .manifest import LoadedProject, Scenario, load_project

ADAPTERS = {"lifecycle":"external@1", "delivery":"http-json@1", "invariants":"declarative@1",
            "dispatcher":"exec@1"}


class ExternalDNA(StrictModel):
    schema_version: Literal["external-dna@1"] = Field(alias="schema")
    dna_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    reproducer: dict[str, Any]
    characterization: dict[str, Any]


def semantics(project: LoadedProject) -> dict[str, Any]:
    delivery = project.manifest.delivery.model_dump(mode="json", exclude={"secret_ref", "header_refs"})
    package = Path(__file__).resolve().parents[1]
    paths = [p for p in package.rglob("*.py") if "resources" not in p.relative_to(package).parts]
    paths += list((package / "semantics").glob("*.json"))
    sources = {str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    implementation = canonical_sha256(sources)
    return {"adapters":{**ADAPTERS, "observer":project.manifest.observation.adapter},
            "implementation_sha256":implementation,
            "contract":project.contract.model_dump(mode="json"),
            "fixtures":project.fixtures,
            "payloads":{name:body.decode("utf-8") for name,body in project.payloads.items()},
            "delivery":{**delivery,"referenced_header_names":sorted(project.manifest.delivery.header_refs)},"normalization":"entities@1",
            "mode":project.manifest.target.mode}


def build_dna(project: LoadedProject, scenario: Scenario, result: dict[str, Any]) -> dict[str, Any]:
    from .services import redact
    aggregate = result["result"]
    if not aggregate["signature"] or not aggregate["fail"]:
        raise ValueError("Failure DNA requires an observed, signed failure")
    reproducer = {**semantics(project),"scenario":scenario.model_dump(mode="json"),
                  "failure_signature":aggregate["signature"],
                  "measured_sample":{"valid":aggregate["valid"],"fail":aggregate["fail"],
                                     "p0":aggregate["fail"]/aggregate["valid"]},
                  "assumptions":["owned isolated test application", "fresh managed process and test state",
                                 "scoped read-only persisted-state evidence", "bounded empirical experiment"]}
    provenance = next((t["before"]["provenance"] for t in result["trials"] if t["verdict"]["status"]=="FAIL"),{})
    reproducer["observed_schema"]={k:provenance.get(k) for k in ("adapter","version","read_only","schema_fingerprint")}
    # Refuse rather than silently changing hashed business data during export.
    from .storage import _redact
    if redact(project, reproducer) != reproducer or _redact(reproducer) != reproducer:
        raise ValueError("portable reproducer contains a resolved credential; remove it from fixtures/configuration")
    return {"schema":"external-dna@1","dna_id":canonical_sha256(reproducer),"reproducer":reproducer,
            "characterization":{"valid":aggregate["valid"],"fail":aggregate["fail"],
                 "p0":aggregate["fail"]/aggregate["valid"],
                 "provenance":"actual local HTTP deliveries and declared database observer"}}


def preflight(payload: dict[str, Any], project: LoadedProject, *, allow_reduction: bool = False) -> Scenario:
    """Checks finish before any process or delivery is started."""
    from .services import precheck
    if not isinstance(payload, dict) or not isinstance(payload.get("reproducer"), dict):
        raise ValueError("artifact must contain a reproducer object")
    if canonical_sha256(payload["reproducer"]) != payload.get("dna_id"):
        raise ValueError("Failure DNA integrity digest mismatch")
    document = ExternalDNA.model_validate(payload)
    expected = semantics(project)
    actual = document.reproducer
    for name, value in expected.items():
        if actual.get(name) != value:
            raise ValueError(f"incompatible external artifact: {name}")
    allowed = set(expected) | {"scenario", "failure_signature", "assumptions", "reduced_from", "measured_sample", "observed_schema"}
    if set(actual) - allowed or not {"scenario", "failure_signature", "assumptions", "measured_sample", "observed_schema"} <= set(actual):
        raise ValueError("unsupported external reproducer fields")
    FailureSignature.model_validate(actual["failure_signature"])
    observed_schema=actual["observed_schema"]
    from .observers import OBSERVER_VERSION
    import re
    if (not isinstance(observed_schema,dict) or set(observed_schema)!={"adapter","version","read_only","schema_fingerprint"}
            or observed_schema.get("read_only") is not True
            or observed_schema.get("version")!=OBSERVER_VERSION
            or observed_schema.get("adapter")!=project.manifest.observation.adapter.split("@")[0]
            or not re.fullmatch(r"[a-f0-9]{64}",str(observed_schema.get("schema_fingerprint","")))):
        raise ValueError("invalid observed schema provenance")
    project.expected_schema=observed_schema["schema_fingerprint"]
    scenario = Scenario.model_validate(actual["scenario"])
    original = project.scenario(scenario.id)
    if "reduced_from" not in actual and scenario != original:
        raise ValueError("scenario differs from the receiving manifest")
    if "reduced_from" in actual:
        _validate_reduction(original, scenario)
    p0 = document.characterization.get("p0")
    if isinstance(p0, bool) or not isinstance(p0, (int,float)) or not 0 < p0 <= 1:
        raise ValueError("invalid measured failure rate")
    sample = actual["measured_sample"]
    if (not isinstance(sample,dict) or set(sample) != {"valid","fail","p0"}
            or type(sample["valid"]) is not int or type(sample["fail"]) is not int
            or not 1 <= sample["fail"] <= sample["valid"] <= 130
            or sample["p0"] != sample["fail"]/sample["valid"]
            or any(document.characterization.get(k) != v for k,v in sample.items())):
        raise ValueError("measured sample metadata is inconsistent")
    precheck(project)
    return scenario


def _validate_reduction(original: Scenario, reduced: Scenario):
    if (reduced.context != original.context or reduced.delivery_policy != original.delivery_policy
            or reduced.acknowledgements != original.acknowledgements or reduced.ack_delivery_id != original.ack_delivery_id
            or reduced.require_overlap != original.require_overlap or reduced.require_retry != original.require_retry
            or reduced.settle_ms != original.settle_ms):
        raise ValueError("reduction changed semantic conditions")
    remaining = {d.id for d in reduced.deliveries}
    if any(d not in original.deliveries for d in reduced.deliveries):
        raise ValueError("reduction changed a delivery")
    steps = []
    for step in original.steps:
        names = [name for name in step.deliveries if name in remaining]
        if names:
            if len(step.deliveries)>1 and len(names)<2:
                raise ValueError("reduction lost concurrency")
            steps.append(step.model_copy(update={"deliveries":names}))
        elif len(step.deliveries)>1 or step.delay_before_ms:
            raise ValueError("reduction lost group or delay semantics")
    if reduced.steps != steps or reduced.expected_status != {k:v for k,v in original.expected_status.items() if k in remaining}:
        raise ValueError("reduction changed order or response expectations")


async def replay(config: Path, payload: dict[str, Any], *, home: Path | None = None) -> dict[str, Any]:
    from .services import run_series
    from .storage import ProjectStore
    project = load_project(config)
    try:
        scenario = preflight(payload, project)
    except ValueError as exc:
        return {"outcome":"REJECTED","reason":str(exc),"executed":False}
    store = ProjectStore(project.manifest.duress.project, home=home)
    result = await run_series(project, scenario, store.home, POLICIES["replay"])
    if any("replay_schema_incompatible" in t["verdict"]["reasons"] for t in result["trials"]):
        return {"outcome":"REJECTED","reason":"replay_schema_incompatible","delivered":False,"result":result}
    signature = payload["reproducer"]["failure_signature"]
    same = [t for t in result["trials"] if t["verdict"].get("signature") == signature and t["verdict"]["status"] == "FAIL"]
    outcome = ("REPRODUCED" if same else "DIFFERENT_FAILURE" if result["result"]["fail"] else
               "NOT_REPRODUCED" if result["result"]["status"] == "PASS" else "INCONCLUSIVE")
    record = {"outcome":outcome,"dna_id":payload["dna_id"],"same_signature_failures":len(same),"result":result}
    store.save("operations", record)
    return record


async def verify(config: Path, candidate_config: Path, payload: dict[str, Any], *, home: Path | None = None) -> dict[str, Any]:
    from ..limits import verify_candidate_policy, verify_control_policy
    from .services import run_series
    from .storage import ProjectStore
    control, candidate = load_project(config), load_project(candidate_config)
    try:
        scenario = preflight(payload, control)
        candidate_scenario = preflight(payload, candidate)
    except ValueError as exc:
        return {"outcome":"REJECTED","reason":str(exc),"executed":False}
    store = ProjectStore(control.manifest.duress.project, home=home)
    p0 = payload["characterization"]["p0"]
    signature = payload["reproducer"]["failure_signature"]
    policy = verify_control_policy(p0)
    baseline = await run_series(control, scenario, store.home, policy)
    same = sum(t["verdict"]["status"] == "FAIL" and t["verdict"].get("signature") == signature for t in baseline["trials"])
    valid = baseline["result"]["valid"]
    rate = same/valid if valid else 0
    record = {"dna_id":payload["dna_id"],"control":baseline,"outcome":"INCONCLUSIVE"}
    n = candidate_size(rate)
    if valid < policy.required_valid or rate < p0/2 or not n:
        record["reason"] = "control_not_reestablished_or_sample_budget_insufficient"
    else:
        sample = await run_series(candidate, candidate_scenario, store.home, verify_candidate_policy(n))
        record.update(candidate=sample, required_candidate_trials=n,
            outcome="NOT_VERIFIED" if sample["result"]["fail"] else "VERIFIED" if sample["result"]["status"] == "PASS" else "INCONCLUSIVE",
            claim="Bounded empirical result for this reproducer and observed control rate; no universal correctness claim.")
    store.save("operations", record)
    return record


async def reduce(config: Path, payload: dict[str, Any], *, home: Path | None = None) -> dict[str, Any]:
    from .services import run_series
    from .storage import ProjectStore
    project = load_project(config)
    try:
        original = preflight(payload, project)
    except ValueError as exc:
        return {"outcome":"REJECTED","reason":str(exc),"executed":False}
    store = ProjectStore(project.manifest.duress.project, home=home)
    candidates = []
    for delivery in original.deliveries:
        data = original.model_dump(mode="json")
        data["deliveries"] = [d for d in data["deliveries"] if d["id"] != delivery.id]
        data["steps"] = [{**step,"deliveries":[n for n in step["deliveries"] if n != delivery.id]}
                         for step in data["steps"] if any(n != delivery.id for n in step["deliveries"])]
        data["expected_status"].pop(delivery.id, None)
        try:
            reduced = Scenario.model_validate(data)
            _validate_reduction(original, reduced)
        except ValueError:
            continue
        result = await run_series(project, reduced, store.home, POLICIES["reduce"])
        candidates.append(result)
        if result["result"].get("signature") == payload["reproducer"]["failure_signature"]:
            confirmed = await run_series(project, reduced, store.home, POLICIES["replay"])
            if confirmed["result"].get("signature") == payload["reproducer"]["failure_signature"]:
                document = build_dna(project, reduced, confirmed)
                document["reproducer"]["reduced_from"] = payload["dna_id"]
                document["dna_id"] = canonical_sha256(document["reproducer"])
                store.save("dna", document, document["dna_id"])
                record = {"outcome":"REDUCED","original_dna_id":payload["dna_id"],
                          "dna":document,"candidates":candidates,"revalidation":confirmed,
                          "claim":"One bounded delivery removal, independently revalidated; no global minimality claim."}
                store.save("operations", record)
                return record
    record = {"outcome":"NOT_REDUCED","original_dna_id":payload["dna_id"],"candidates":candidates,
              "reason":"No removal preserved concurrency, semantics and independently reproduced signature."}
    store.save("operations", record)
    return record
