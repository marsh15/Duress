"""Investigation accounting at the real orchestration boundary."""
from __future__ import annotations

import pytest

from duress import dna, orchestrator
from duress.models import AggregateVerdict, FailureSignature, ScenarioResult
from duress.orchestrator import InvestigationContext, run_investigation
from duress.provider_stub import StubServer


@pytest.mark.asyncio
async def test_failed_characterization_trials_count_toward_investigation_cap(registry, tmp_path, monkeypatch):
    signature = FailureSignature(check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"])

    async def explore(config, **_):
        return ScenarioResult(
            template_id=config.scenario.template_id,
            scenario_id=config.scenario.scenario_id,
            result=AggregateVerdict(
                status="FAIL", valid=5, fail=5, trials=5,
                signature=signature, signature_counts={signature.key: 5},
            ),
        )

    async def characterize(*args, **kwargs):
        return ScenarioResult(
            template_id="S1", scenario_id="S1",
            result=AggregateVerdict(status="PASS", valid=10, trials=10),
        )

    monkeypatch.setattr(orchestrator, "run_scenario", explore)
    monkeypatch.setattr(dna, "run_scenario", characterize)
    record = await run_investigation(
        InvestigationContext(
            profile=registry.get("razorpay-vulnerable"), home=tmp_path,
            workdir_root=tmp_path, stub=StubServer(), root=tmp_path,
        ),
        mode="exhaustive",
    )
    assert record["status"] == "completed", record["errors"]
    assert record["trials_used"] == 7 * 5 + 10
    assert record["findings"][0]["dna_error"].startswith("characterization_did_not_fail")
    assert record["dna_ids"] == []


@pytest.mark.asyncio
async def test_characterization_of_another_failure_is_not_attached_to_the_finding(registry, tmp_path, monkeypatch):
    from types import SimpleNamespace

    signature = FailureSignature(check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"])
    other = FailureSignature(check="INV-4@1", kind="captured_without_fulfilment", roles=["payment:A1"])

    async def explore(config, **_):
        return ScenarioResult(
            template_id=config.scenario.template_id, scenario_id=config.scenario.scenario_id,
            result=AggregateVerdict(status="FAIL", valid=5, fail=5, trials=5, signature=signature),
        )

    async def characterize(*args, **kwargs):
        result = ScenarioResult(
            template_id="S1", scenario_id="S1",
            result=AggregateVerdict(status="FAIL", valid=10, fail=10, trials=10, signature=other),
        )
        return object(), SimpleNamespace(signature=other, scenario_result=result)

    monkeypatch.setattr(orchestrator, "run_scenario", explore)
    monkeypatch.setattr(dna, "build_dna", characterize)
    record = await run_investigation(
        InvestigationContext(
            profile=registry.get("razorpay-vulnerable"), home=tmp_path,
            workdir_root=tmp_path, stub=StubServer(), root=tmp_path,
        ), mode="exhaustive",
    )
    assert record["status"] == "completed", record["errors"]
    assert record["trials_used"] == 45
    assert record["findings"][0]["dna_error"] == "characterisation_different_signature"
    assert record["dna_ids"] == []


@pytest.mark.asyncio
async def test_confirmed_hypothesis_is_not_linked_to_a_different_finding(registry, tmp_path, monkeypatch):
    from duress import investigator
    from duress.models import TrialVerdict, Violation
    from duress.profiles import load_seed

    profile = registry.get("razorpay-vulnerable")
    draft = investigator.HypothesisDraft.model_validate({
        "id": "H1", "title": "duplicate fulfilment", "mechanism": "Two deliveries race",
        "mechanism_kind": "other",
        "citations": [{"file": "razorpay_ref/vulnerable/handler.py", "line_start": 1, "line_end": 1}],
        "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
        "scenarios": [{"template_id": "S1"}, {"template_id": "S3"}],
    })
    hypothesis = investigator.assess(draft, investigator.build_source_bundle(profile), profile.roles, load_seed(profile))
    assert hypothesis.usable

    async def model(*_):
        return orchestrator.ModelPhase(hypotheses=[hypothesis])

    async def explore(config, **_):
        scenario = config.scenario
        check, kind = ("INV-1@1", "duplicate_fulfilment") if scenario.template_id == "S3" else ("INV-6@1", "unexpected_mutation")
        signature = FailureSignature(check=check, kind=kind, roles=["order:A"])
        violation = Violation(check=check, kind=kind, roles=["order:A"])
        return ScenarioResult(
            template_id=scenario.template_id, scenario_id=scenario.scenario_id,
            result=AggregateVerdict(status="FAIL", valid=1, fail=1, trials=1, signature=signature),
            verdicts=[TrialVerdict(status="FAIL", signature=signature, violations=[violation])],
        )

    monkeypatch.setattr(orchestrator, "_model_phase", model)
    monkeypatch.setattr(orchestrator, "run_scenario", explore)
    record = await run_investigation(
        InvestigationContext(
            profile=profile, home=tmp_path, workdir_root=tmp_path,
            stub=StubServer(), root=tmp_path, wall_cap_s=0,
        ), mode="cached",
    )
    assert record["status"] == "completed", record["errors"]
    assert record["hypotheses"][0]["status"]["outcome"] == "confirmed"
    by_check = {finding["signature"]["check"]: finding for finding in record["findings"]}
    assert by_check["INV-1@1"]["hypothesis_ids"] == ["H1"]
    assert by_check["INV-6@1"]["hypothesis_ids"] == []
