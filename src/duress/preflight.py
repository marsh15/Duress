"""Preflight: reject an untrusted DNA before any target starts (plan C.10).

Every check runs in order and the first failure is the answer. An artifact is
*integrity*-checked, not authenticated: the digest proves the bytes are the ones
that were characterised, not who wrote them. That limitation is documented in
README and DECISIONS.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import dna, semantics
from . import limits as limits_module
from .invariants import MEMBERS, member_by_id
from .models import (
    BuiltScenario,
    DnaDocument,
    PreflightRejected,
    Schedule,
    canonical_sha256,
)
from .profiles import TargetProfile, load_seed
from .schedule import validate_schedule
from .target import _base_env, probe_schema

SUPPORTED_SCHEMA = 1
EXPECTED_EFFECT_CHECK = "expected-effect@1"


@dataclass(frozen=True)
class PreflightOptions:
    """Preflight knobs.

    ``secret_required`` is False by default because a managed trial provisions
    its own webhook secret and injects it into both the target and the signer.
    A caller that demands the environment value (for example an external
    operator) can require it, and an unresolvable ref scheme always fails.
    """

    secret_required: bool = False
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class PreflightOutcome:
    rejected: PreflightRejected | None
    document: DnaDocument | None = None
    scenario: BuiltScenario | None = None
    secret: str | None = None

    @property
    def ok(self) -> bool:
        return self.rejected is None


def _reject(reason: str, detail: str = "") -> PreflightOutcome:
    return PreflightOutcome(rejected=PreflightRejected(reason=reason, detail=detail))


def _canonical_member_map(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        f"{member.get('id')}@{member.get('version')}": member
        for member in pack.get("members", [])
    }


def resolve_secret(ref: Any, env: dict[str, str], *, required: bool) -> str | None:
    if not isinstance(ref, str) or ":" not in ref:
        raise ValueError("secret_ref must be '<scheme>:<name>'")
    scheme, _, name = ref.partition(":")
    if scheme != "env" or not name:
        raise ValueError(f"unsupported secret_ref scheme {scheme!r}")
    value = env.get(name)
    if value:
        return value
    if required:
        raise KeyError(f"secret_ref {ref!r} is not set")
    return None


def _local_expectation(document: DnaDocument, profile: TargetProfile):
    """The registry entry this expectation should match, or None."""
    provenance = document.provenance_scenario
    seed = load_seed(profile)
    roles = dict(document.reproducer.get("seed", {}).get("roles", {}))
    from .scenarios import TEMPLATES, build_scenario

    attempts: list[tuple[str, dict[str, Any]]] = []
    if provenance.get("template_id") in TEMPLATES:
        attempts.append(
            (str(provenance["template_id"]), dict(provenance.get("params") or {}))
        )
    for template_id in TEMPLATES:
        if template_id == provenance.get("template_id"):
            continue
        attempts.append((template_id, {}))
    wanted_id = document.reproducer["expectation"]["id"]
    for template_id, params in attempts:
        try:
            built = build_scenario(template_id, params, roles, seed)
        except (KeyError, ValueError):
            continue
        if built.expectation.id == wanted_id:
            return built.expectation
    return None


def _check_expectation(document: DnaDocument, profile: TargetProfile) -> PreflightOutcome | None:
    embedded = document.reproducer.get("expectation") or {}
    definition = embedded.get("definition")
    claimed = embedded.get("definition_sha256")
    if not isinstance(definition, dict) or not isinstance(claimed, str):
        return _reject("tampered:expectation", "expectation definition missing")
    if canonical_sha256(definition) != claimed:
        return _reject("tampered:expectation", "definition does not hash to its claim")
    local = _local_expectation(document, profile)
    if local is None:
        return _reject("incompatible:expectation", "no local registry entry")
    if (
        local.id != embedded.get("id")
        or local.version != embedded.get("version")
        or local.definition() != definition
    ):
        return _reject(
            "incompatible:expectation",
            f"local {local.id}@{local.version} differs from the embedded definition",
        )
    return None


def _check_invariant_pack(document: DnaDocument) -> PreflightOutcome | None:
    embedded = document.reproducer.get("invariant_pack") or {}
    members = embedded.get("members")
    if not isinstance(members, list) or not members:
        return _reject("tampered:invariant_pack", "pack members missing")

    identities: list[str] = []
    for member in members:
        definition = member.get("definition")
        claimed = member.get("definition_sha256")
        if not isinstance(definition, dict) or not isinstance(claimed, str):
            return _reject("tampered:invariant_pack", "member definition missing")
        if canonical_sha256(definition) != claimed:
            return _reject(
                "tampered:invariant_pack",
                f"{member.get('id')} definition does not hash to its claim",
            )
        identities.append(f"{member.get('id')}@{member.get('version')}#{claimed}")

    if canonical_sha256(sorted(identities)) != embedded.get("digest"):
        return _reject("tampered:invariant_pack", "pack digest does not recompute")

    local_pack = dna.invariant_pack_ref()
    if embedded.get("id") != local_pack["id"] or embedded.get("version") != local_pack["version"]:
        return _reject(
            "incompatible:invariant_pack",
            f"pack {embedded.get('id')}@{embedded.get('version')} is not supported",
        )

    embedded_map = _canonical_member_map(embedded)
    local_map = _canonical_member_map(local_pack)
    for key, local_member in local_map.items():
        if key not in embedded_map:
            return _reject("incompatible:invariant_pack", f"member {key} is missing")
        if embedded_map[key].get("definition_sha256") != local_member.get(
            "definition_sha256"
        ) or embedded_map[key].get("definition") != local_member.get("definition"):
            return _reject("incompatible:invariant_pack", f"member {key} differs")
    if set(embedded_map) - set(local_map):
        extra = ", ".join(sorted(set(embedded_map) - set(local_map)))
        return _reject("incompatible:invariant_pack", f"unknown member(s): {extra}")

    signature = document.reproducer.get("failure_signature") or {}
    check = signature.get("check")
    allowed = {member.check for member in MEMBERS} | {EXPECTED_EFFECT_CHECK}
    if check not in allowed:
        return _reject("incompatible:invariant_pack", f"signature check {check!r} unknown")
    return None


def _check_schedule(document: DnaDocument, limits: limits_module.Limits) -> tuple[
    PreflightOutcome | None, Schedule | None
]:
    try:
        schedule = Schedule.model_validate(document.reproducer.get("schedule"))
    except Exception as exc:  # pydantic validation detail is enough here
        return _reject("invalid:schedule", f"{type(exc).__name__}: {exc}"), None
    errors = validate_schedule(schedule, limits)
    if errors:
        return _reject("invalid:schedule", "; ".join(errors)), None
    return None, schedule


async def _check_target_contract(
    document: DnaDocument, profile: TargetProfile
) -> PreflightOutcome | None:
    contract = document.reproducer.get("target_contract") or {}
    if contract.get("profile") != profile.profile_id:
        return _reject(
            "incompatible:target_contract",
            f"artifact targets {contract.get('profile')!r}, runtime has {profile.profile_id!r}",
        )
    if contract.get("contract_version") != profile.contract_version:
        return _reject(
            "incompatible:target_contract",
            f"contract v{contract.get('contract_version')} vs v{profile.contract_version}",
        )
    workers = (contract.get("process") or {}).get("workers")
    if workers != profile.workers:
        return _reject(
            "incompatible:target_contract",
            f"workers {workers} vs {profile.workers}",
        )

    with tempfile.TemporaryDirectory(prefix="duress-preflight-") as scratch:
        db_path = Path(scratch) / "app.db"
        command = profile.format_command(
            profile.init_command,
            db=db_path,
            seed=profile.seed_path,
            host="127.0.0.1",
            port=0,
        )
        try:
            process = subprocess.run(
                command,
                cwd=str(profile.cwd),
                env=_base_env(profile),
                capture_output=True,
                timeout=limits_module.DEFAULT_LIMITS.phase("prepare"),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return _reject("schema_mismatch", f"init-db failed to run: {exc}")
        if process.returncode != 0:
            detail = process.stderr.decode(errors="replace").strip()[-400:]
            return _reject("schema_mismatch", f"init-db exited {process.returncode}: {detail}")
        ok, error = probe_schema(db_path)
        if not ok:
            return _reject("schema_mismatch", error or "schema probe failed")
    return None


def preflight_payload(
    payload: Any,
    *,
    profile: TargetProfile,
    limits: limits_module.Limits | None = None,
    options: PreflightOptions | None = None,
) -> PreflightOutcome:
    """Synchronous preflight for raw JSON payloads.

    Contract checks that need a target process (step 6) are performed by
    ``preflight_async``; this function covers steps 1-5, 7 and 8 so a caller that
    only wants integrity can use it.
    """
    limits = limits or limits_module.DEFAULT_LIMITS
    options = options or PreflightOptions()

    if not isinstance(payload, dict):
        return _reject("invalid:document", "DNA must be a JSON object")
    if payload.get("schema_version") != SUPPORTED_SCHEMA:
        return _reject(
            "unsupported_version", f"schema_version={payload.get('schema_version')!r}"
        )
    try:
        document = dna.document_from_json(payload)
    except Exception as exc:
        return _reject("invalid:document", f"{type(exc).__name__}: {exc}")

    reproducer = document.reproducer
    actual = canonical_sha256(reproducer)
    if actual != document.reproducer_sha256:
        return _reject("tampered:digest", f"reproducer hashes to {actual[:16]}")
    if not actual.startswith(document.dna_id) or document.dna_id != actual[:16]:
        return _reject("tampered:digest", "dna_id is not the digest prefix")

    rejected = _check_expectation(document, profile)
    if rejected:
        return rejected
    rejected = _check_invariant_pack(document)
    if rejected:
        return rejected

    ref = reproducer.get("execution_semantics") or {}
    if not semantics.supports(
        ref.get("id"), ref.get("version"), ref.get("digest") or ""
    ):
        return _reject(
            "incompatible:execution_semantics",
            f"{ref.get('id')}@{ref.get('version')} digest {str(ref.get('digest'))[:16]}",
        )

    rejected, schedule = _check_schedule(document, limits)
    if rejected:
        return rejected

    if not dna.seed_matches(document, load_seed(profile)):
        return _reject("incompatible:target_contract", "seed baseline differs")

    env = dict(options.env or os.environ)
    try:
        secret = resolve_secret(
            (reproducer.get("signing") or {}).get("secret_ref"),
            env,
            required=options.secret_required,
        )
    except (KeyError, ValueError) as exc:
        return _reject("secret_unresolved", str(exc))

    scenario = dna.scenario_from_document(document, profile)
    if document.reproducer.get("release_condition") not in {"parties", "timeout", "none"}:
        return _reject("invalid:schedule", "release_condition is not defined")
    if set(document.reproducer["seed"]["roles"]) != set(scenario.roles):
        return _reject("invalid:document", "seed roles were altered")
    if document.reproducer.get("domain") != dna.DOMAIN:
        return _reject("incompatible:target_contract", "domain is not payments-webhook")

    return PreflightOutcome(
        rejected=None, document=document, scenario=scenario, secret=secret
    )


async def preflight_async(
    payload: Any,
    *,
    profile: TargetProfile,
    limits: limits_module.Limits | None = None,
    options: PreflightOptions | None = None,
) -> PreflightOutcome:
    outcome = preflight_payload(payload, profile=profile, limits=limits, options=options)
    if outcome.rejected:
        return outcome
    assert outcome.document is not None
    rejected = await _check_target_contract(outcome.document, profile)
    if rejected:
        return rejected
    return outcome