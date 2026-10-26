"""M3 acceptance: no usable hypotheses -> the real exhaustive set runs (plan C.12).

A fake OpenAI-compatible transport returns well-formed hypotheses whose
citations are all wrong. The investigator must reject every one, keep them in
the record, and execute the seven real ``EXHAUSTIVE_SET`` scenarios through the
trusted runner against a real managed target. Nothing about the templates is
mocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from duress import limits as limits_module
from duress.llm import LlmCache, LlmConfig
from duress.profiles import ProfileRegistry
from duress.scenarios import EXHAUSTIVE_SET
from duress.services import Workspace, investigate
from duress.store import ensure_dirs

ROOT = Path(__file__).resolve().parents[2]

BAD_CITATIONS = {
    "hypotheses": [
        {
            "id": "H1",
            "title": "Check-then-act race on fulfilment",
            "mechanism": "Concurrent deliveries both pass the status check.",
            "mechanism_kind": "check_then_act_race",
            # A file that is not in the bundle.
            "citations": [{"file": "razorpay_ref/vulnerable/missing.py", "line_start": 1, "line_end": 3}],
            "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
            "scenarios": [{"template_id": "S3", "params": {"copies": 3, "fault": "rendezvous"}}],
        },
        {
            "id": "H2",
            "title": "Cross-event race",
            "mechanism": "payment.captured and order.paid race.",
            "mechanism_kind": "check_then_act_race",
            # A real file, but lines far beyond its end.
            "citations": [{"file": "razorpay_ref/vulnerable/handler.py", "line_start": 9000, "line_end": 9010}],
            "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
            "scenarios": [{"template_id": "S5", "params": {"fault": "rendezvous"}}],
        },
        {
            "id": "H3",
            "title": "Quote that is not in the source",
            "mechanism": "Fabricated excerpt.",
            "mechanism_kind": "other",
            "citations": [
                {
                    "file": "razorpay_ref/vulnerable/handler.py",
                    "line_start": 1,
                    "line_end": 5,
                    "quote": "this text does not appear anywhere in the handler",
                }
            ],
            "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
            "scenarios": [{"template_id": "S3", "params": {"copies": 2, "fault": "latency"}}],
        },
    ]
}


@pytest.mark.asyncio
async def test_all_citations_rejected_falls_back_to_real_exhaustive_set(tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": json.dumps(BAD_CITATIONS)}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        )

    home = tmp_path / ".duress"
    ensure_dirs(home)
    workspace = Workspace(
        root=ROOT,
        home=home,
        registry=ProfileRegistry.discover(ROOT),
        limits=limits_module.DEFAULT_LIMITS,
    )
    record = await investigate(
        workspace,
        target_id="razorpay-vulnerable",
        mode="live",
        llm_config=LlmConfig(base_url="http://llm.invalid/v1", model="fake-model", api_key="k"),
        llm_transport=httpx.MockTransport(handler),
        cache=LlmCache([tmp_path / "empty-cache"], write_dir=tmp_path / "llm-cache"),
    )

    assert record["status"] == "completed", record["errors"]
    assert len(calls) == 1, "valid JSON needs no repair call"
    assert record["mode_requested"] == "live"
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "no_usable_hypotheses"

    # Rejected hypotheses stay visible, each with the reason it was rejected.
    hypotheses = record["hypotheses"]
    assert [item["draft"]["id"] for item in hypotheses] == ["H1", "H2", "H3"]
    assert record["usable_hypotheses"] == 0
    assert record["rejected_hypotheses"] == 3
    for item in hypotheses:
        assert item["usable"] is False
        assert item["status"]["citation"] == "invalid"
        assert item["status"]["citation_errors"]
        assert item["status"]["outcome"] == "not_run"
        assert item["source"] == "model"

    # The seven real registry scenarios ran, each with at least one trial.
    assert len(EXHAUSTIVE_SET) == 7
    assert set(record["executed_scenario_ids"]) == set(EXHAUSTIVE_SET)
    assert len(record["executed_scenario_ids"]) == 7
    for entry in record["scenarios"]:
        assert entry["selected_by"] == "exhaustive"
        assert entry["result"] is not None
        assert entry["result"]["trials"] >= 1
        assert len(entry["trials"]) == entry["result"]["trials"]

    # The vulnerable target's race is found by execution, and data-only text explains it.
    assert record["verdict"] == "FAIL"
    assert record["explanation_source"] == "template"
    assert record["trials_used"] <= limits_module.INVESTIGATION_TRIAL_CAP

    # The record on disk is the record returned.
    stored = json.loads((home / "investigations" / f"{record['investigation_id']}.json").read_text())
    assert stored["fallback_reason"] == "no_usable_hypotheses"
    assert stored["executed_scenario_ids"] == record["executed_scenario_ids"]
