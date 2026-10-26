"""Failure DNA: characterise a verified failure, then serialise it (plan C.9).

A DNA is built only from trials that ran. It carries the complete reproducer —
schedule, expectation, provider fixtures, seed roles, the whole invariant pack,
the execution-semantics identity and the failure signature — so replay needs no
access to the scenario registry entry that produced it.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import semantics
from . import limits as limits_module
from .invariants import MEMBERS, PACK_ID, PACK_VERSION, pack_digest
from .models import (
    BuiltScenario,
    DnaDocument,
    FailureSignature,
    ScenarioResult,
    Schedule,
    SeedDocument,
    canonical_dict,
    canonical_sha256,
)
from .operations import OperationConfig, run_scenario
from .profiles import TargetProfile, load_seed
from .provider_stub import StubServer

DNA_SCHEMA_VERSION = 1
TARGET_CONTRACT_VERSION = 1
DOMAIN = "payments-webhook"


class DnaError(RuntimeError):
    """The failure could not be characterised into a DNA."""

    def __init__(self, message: str, *, scenario_result: ScenarioResult | None = None) -> None:
        super().__init__(message)
        self.scenario_result = scenario_result


# --------------------------------------------------------------------------- #
# Identity helpers
# --------------------------------------------------------------------------- #
def execution_semantics_ref() -> dict[str, Any]:
    document = semantics.spec()
    return {
        "id": document["id"],
        "version": document["version"],
        "digest": semantics.digest(),
    }


def signing_ref(profile: TargetProfile) -> dict[str, str]:
    return {
        "scheme": profile.signing.scheme,
        "header": profile.signing.header,
        "event_id_header": profile.signing.event_id_header,
        "secret_ref": profile.signing.secret_ref,
    }


def target_contract_ref(profile: TargetProfile) -> dict[str, Any]:
    return {
        "profile": profile.profile_id,
        "contract_version": profile.contract_version,
        "process": {"workers": profile.workers},
    }


def invariant_pack_ref() -> dict[str, Any]:
    return {
        "id": PACK_ID,
        "version": PACK_VERSION,
        "digest": pack_digest(),
        "members": [
            {
                "id": member.id,
                "version": member.version,
                "definition_sha256": member.definition_sha256(),
                "definition": member.definition(),
            }
            for member in MEMBERS
        ],
    }


def expectation_ref(scenario: BuiltScenario) -> dict[str, Any]:
    expectation = scenario.expectation
    return {
        "id": expectation.id,
        "version": expectation.version,
        "definition_sha256": expectation.definition_sha256(),
        "definition": expectation.definition(),
    }


def provider_fixture_list(fixtures: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(fixtures[key]) for key in sorted(fixtures)]


def control_level(schedule: Schedule) -> str:
    """C.3: faults are the only source of controlled concurrency."""
    return "black_box" if not schedule.faults else "environment_controlled"


def target_source_sha256(profile: TargetProfile) -> str:
    """Hash the target bytes a replay's result should be attributed to."""
    root = profile.target_dir
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".db", ".db-wal", ".db-shm"}:
            continue
        relative = path.relative_to(root).as_posix()
        entries.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return canonical_sha256(entries)


def environment_ref(profile: TargetProfile) -> dict[str, str]:
    from . import __version__

    return {
        "duress_version": __version__,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "target_source_sha256": target_source_sha256(profile),
    }


# --------------------------------------------------------------------------- #
# Characterisation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Characterisation:
    scenario_result: ScenarioResult
    signature: FailureSignature
    valid: int
    fail: int
    p0: float
    release_conditions: dict[str, int]
    representative_trial: int

    @property
    def release_condition(self) -> str:
        values = {key for key, count in self.release_conditions.items() if count}
        if not values:
            return "none"
        if len(values) == 1:
            return values.pop()
        return "mixed"


def _release_conditions(result: ScenarioResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for observation in result.observations:
        key = observation.stub.released_by or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _representative_index(result: ScenarioResult, signature: FailureSignature) -> int:
    for index, verdict in enumerate(result.verdicts):
        if verdict.status == "FAIL" and verdict.signature and verdict.signature.key == signature.key:
            return index
    for index, verdict in enumerate(result.verdicts):
        if verdict.status == "FAIL" and verdict.signature:
            return index
    raise DnaError("no failing trial carries a signature")


async def characterise(
    profile: TargetProfile,
    scenario: BuiltScenario,
    stub: StubServer,
    *,
    workdir_root: Path,
    home: Path | None = None,
    limits: limits_module.Limits | None = None,
    wall_deadline: float | None = None,
) -> Characterisation:
    """Run the frozen ``characterize`` policy and require a real FAIL.

    A DNA is never written from a single hand-picked trial: p0 is the failure
    rate of the trials that actually ran.
    """
    result = await run_scenario(
        OperationConfig(
            profile=profile,
            scenario=scenario,
            stub=stub,
            workdir_root=workdir_root,
            policy=limits_module.POLICIES["characterize"],
            limits=limits or limits_module.DEFAULT_LIMITS,
            home=home,
            keep_evidence=True,
        ),
        wall_deadline=wall_deadline,
    )
    if result.result.status != "FAIL" or result.result.signature is None:
        raise DnaError(
            "characterization_did_not_fail:"
            f"{result.result.status}:{','.join(result.result.reasons)}",
            scenario_result=result,
        )
    valid = result.result.valid
    if valid <= 0:
        raise DnaError("characterization_no_valid_trials", scenario_result=result)
    return Characterisation(
        scenario_result=result,
        signature=result.result.signature,
        valid=valid,
        fail=result.result.fail,
        p0=round(result.result.fail / valid, 4),
        release_conditions=_release_conditions(result),
        representative_trial=_representative_index(result, result.result.signature),
    )


# --------------------------------------------------------------------------- #
# Reproducer and document
# --------------------------------------------------------------------------- #
def build_reproducer(
    profile: TargetProfile,
    scenario: BuiltScenario,
    signature: FailureSignature,
    *,
    release_condition: str,
    replay_required: int = 10,
) -> dict[str, Any]:
    seed = load_seed(profile)
    return {
        "domain": DOMAIN,
        "execution_semantics": execution_semantics_ref(),
        "target_contract": target_contract_ref(profile),
        "seed": {
            "roles": dict(sorted(scenario.roles.items())),
            "tables": seed.tables(),
        },
        "provider_fixtures": provider_fixture_list(scenario.fixtures),
        "signing": signing_ref(profile),
        "schedule": canonical_dict(scenario.schedule),
        "expectation": expectation_ref(scenario),
        "invariant_pack": invariant_pack_ref(),
        "failure_signature": canonical_dict(signature),
        "release_condition": release_condition,
        "control_level": control_level(scenario.schedule),
        "trial_policy": {"replay": {"required_valid": replay_required}},
    }


def _observation_summary(characterisation: Characterisation) -> dict[str, Any]:
    result = characterisation.scenario_result
    index = characterisation.representative_trial
    verdict = result.verdicts[index]
    observation = result.observations[index]
    evidence = Path(".duress") / "evidence" / observation.trial_id
    return {
        "violation_rows": [canonical_dict(item) for item in verdict.violations],
        "expected_effect_misses": [
            canonical_dict(item) for item in verdict.expected_effect_misses
        ],
        "attempts": [
            {
                "delivery_id": attempt.delivery_id,
                "attempt": attempt.attempt,
                "outcome": attempt.outcome,
                "status_code": attempt.status_code,
                "body_status": attempt.body_status,
                "body_scope": attempt.body_scope,
                "started_s": round(attempt.started_s, 4),
                "ended_s": round(attempt.ended_s, 4),
            }
            for attempt in observation.attempts
        ],
        "release_conditions": observation.stub.release_conditions,
        "evidence_ref": evidence.as_posix(),
        "trial_id": observation.trial_id,
        "settlement": observation.settlement,
    }


def build_document(
    profile: TargetProfile,
    scenario: BuiltScenario,
    characterisation: Characterisation,
    *,
    provenance: dict[str, Any] | None = None,
) -> DnaDocument:
    reproducer = build_reproducer(
        profile,
        scenario,
        characterisation.signature,
        release_condition=characterisation.release_condition,
        replay_required=10,
    )
    digest = canonical_sha256(reproducer)
    document = DnaDocument(
        schema_version=DNA_SCHEMA_VERSION,
        dna_id=digest[:16],
        reproducer_sha256=digest,
        reproducer=reproducer,
        provenance_scenario={
            "template_id": scenario.template_id,
            "template_version": scenario.template_version,
            "scenario_id": scenario.scenario_id,
            "params": dict(scenario.params),
        },
        environment=environment_ref(profile),
        characterization={
            "p0": characterisation.p0,
            "valid": characterisation.valid,
            "fail": characterisation.fail,
            "release_conditions": characterisation.release_conditions,
            "trial_policy": "characterize",
        },
        observations=_observation_summary(characterisation),
        reduction=None,
        provenance={
            "investigation_id": None,
            "hypothesis_id": None,
            "reduced_from": None,
            "target_id": profile.target_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **(provenance or {}),
        },
    )
    return document


async def build_dna(
    profile: TargetProfile,
    scenario: BuiltScenario,
    stub: StubServer,
    *,
    workdir_root: Path,
    home: Path | None = None,
    limits: limits_module.Limits | None = None,
    provenance: dict[str, Any] | None = None,
    wall_deadline: float | None = None,
) -> tuple[DnaDocument, Characterisation]:
    characterisation = await characterise(
        profile,
        scenario,
        stub,
        workdir_root=workdir_root,
        home=home,
        limits=limits,
        wall_deadline=wall_deadline,
    )
    document = build_document(
        profile, scenario, characterisation, provenance=provenance
    )
    return document, characterisation


# --------------------------------------------------------------------------- #
# Reconstruction
# --------------------------------------------------------------------------- #
def scenario_from_document(document: DnaDocument, target_profile: TargetProfile) -> BuiltScenario:
    """Rebuild the executable scenario from the DNA alone.

    The seed roles come from the reproducer; the seed *rows* are verified
    against the target profile by preflight, so a DNA cannot silently run
    against a different baseline.
    """
    reproducer = document.reproducer
    schedule = Schedule.model_validate(reproducer["schedule"])
    expectation = document.expectation()
    fixtures = {
        item["id"]: dict(item) for item in reproducer.get("provider_fixtures", [])
    }
    provenance_scenario = document.provenance_scenario
    return BuiltScenario(
        template_id=str(provenance_scenario.get("template_id", "?")),
        template_version=int(provenance_scenario.get("template_version", 1)),
        scenario_id=str(provenance_scenario.get("scenario_id", document.dna_id)),
        params=dict(provenance_scenario.get("params", {})),
        schedule=schedule,
        expectation=expectation,
        fixtures=fixtures,
        roles=dict(reproducer["seed"]["roles"]),
    )


def document_from_json(payload: Any) -> DnaDocument:
    if not isinstance(payload, dict):
        raise ValueError("DNA document must be a JSON object")
    return DnaDocument.model_validate(payload)


def document_to_json(document: DnaDocument) -> str:
    return json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def seed_matches(document: DnaDocument, seed: SeedDocument) -> bool:
    recorded = document.reproducer.get("seed", {}).get("tables")
    if not recorded:
        return True  # older documents may omit the baseline
    return recorded == seed.tables()


def python_ok() -> bool:
    return sys.version_info >= (3, 12)