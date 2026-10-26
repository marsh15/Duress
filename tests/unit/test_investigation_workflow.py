from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from duress.web import install_web


def _client(tmp_path: Path) -> TestClient:
    home = tmp_path / ".duress"
    for directory in ("investigations", "dna", "operations"):
        (home / directory).mkdir(parents=True, exist_ok=True)
    targets = [
        SimpleNamespace(
            target_id="fixture-modes",
            profile_id="fixture-modes",
            variant="control",
            domain="fixture",
            contract_version=1,
            workers=1,
        ),
        SimpleNamespace(
            target_id="razorpay-vulnerable",
            profile_id="razorpay-ref",
            variant="vulnerable",
            domain="payments-webhook",
            contract_version=1,
            workers=1,
        ),
    ]
    workspace = SimpleNamespace(
        root=tmp_path,
        home=home,
        registry=SimpleNamespace(all=lambda: targets),
    )
    app = FastAPI()
    install_web(app, lambda: workspace)
    return TestClient(app)


def _running_record() -> dict:
    return {
        "record_type": "investigation",
        "investigation_id": "inv-run-01",
        "target_id": "razorpay-vulnerable",
        "status": "running",
        "phase": "explore",
        "mode_requested": "cached",
        "mode_effective": "exhaustive",
        "fallback_reason": "cache_miss",
        "fallback_detail": "No matching authored fixture was available.",
        "created_at": "2026-10-27T08:00:00Z",
        "updated_at": "2026-10-27T08:00:03Z",
        "model": {
            "model": "duress-authored-fixture",
            "response_source": "cache",
            "provenance": {"source": "authored_fixture"},
            "budget": {
                "calls": 1,
                "max_calls": 3,
                "input_tokens": 812,
                "max_input_tokens": 12000,
            },
        },
        "bundle": {"sha256": "a" * 64, "files": ["handler.py"], "bytes": 100},
        "hypotheses": [
            {
                "draft": {
                    "id": "H1",
                    "title": "Concurrent duplicate fulfilment",
                    "mechanism": "The check and fulfilment write can race.",
                    "citations": [
                        {
                            "file": "src/handler.py",
                            "line_start": 18,
                            "line_end": 21,
                            "quote": "if payment_exists(payment_id): return duplicate",
                        }
                    ],
                    "predicted_failure": {
                        "check": "INV-1@1",
                        "kind": "duplicate_fulfilment",
                        "role": "order:A",
                    },
                    "scenarios": [{"template_id": "S3", "params": {"fault": "rendezvous"}}],
                },
                "status": {
                    "citation": "valid",
                    "structure": "pass",
                    "structure_detail": "The handler has a check-then-act window.",
                    "linkage": "valid",
                    "outcome": "not_run",
                    "outcome_detail": "Scenario still running.",
                },
                "usable": True,
                "source": "model",
            }
        ],
        "selected_scenarios": ["S3-rendezvous", "S5-rendezvous"],
        "scenarios": [
            {
                "template_id": "S3",
                "scenario_id": "S3-rendezvous",
                "params": {"copies": 3, "fault": "rendezvous"},
                "selected_by": "hypothesis",
                "status": "running",
                "result": {
                    "status": "FAIL",
                    "valid": 1,
                    "fail": 1,
                    "inconclusive": 0,
                    "trials": 1,
                    "signature": {
                        "check": "INV-1@1",
                        "kind": "duplicate_fulfilment",
                        "roles": ["order:A"],
                    },
                },
                "control_level": "environment_controlled",
                "schedule": {
                    "deliveries": [
                        {
                            "id": "d1",
                            "event": {"type": "payment.captured", "order_role": "order:A", "payment_role": "payment:A1"},
                            "effect_intent": "capture_and_fulfil",
                            "event_id": "evt_same",
                        },
                        {
                            "id": "d2",
                            "event": {"type": "payment.captured", "order_role": "order:A", "payment_role": "payment:A1"},
                            "effect_intent": "capture_and_fulfil",
                            "event_id": "evt_same",
                        },
                    ],
                    "steps": [{"deliveries": ["d1", "d2"], "delay_before_ms": 0}],
                    "faults": [{"kind": "provider_rendezvous", "parties": 2, "timeout_ms": 2000}],
                    "delivery_policy": {"max_attempts": 1, "attempt_timeout_ms": 5000},
                },
                "trials": [
                    {
                        "trial_id": "trial-kept",
                        "status": "FAIL",
                        "reasons": ["duplicate_fulfilment"],
                        "signature": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "roles": ["order:A"]},
                        "settlement": "clean",
                        "released_by": "parties",
                        "duration_s": 1.23,
                    }
                ],
            },
            {
                "template_id": "S5",
                "scenario_id": "S5-rendezvous",
                "params": {"fault": "rendezvous"},
                "selected_by": "exhaustive",
                "status": "pending",
                "result": {"status": "recorded", "valid": 0, "fail": 0, "inconclusive": 0, "trials": 0},
            },
        ],
        "findings": [
            {
                "scenario_id": "S3-rendezvous",
                "signature": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "roles": ["order:A"]},
                "fail": 1,
                "valid": 1,
                "hypothesis_ids": ["H1"],
                "explanation": "One valid scenario trial reproduced the failure signature.",
            }
        ],
        "dna_ids": [],
        "trials_used": 3,
        "trial_cap": 130,
        "wall_cap_s": 440,
        "duration_s": None,
        "verdict": None,
        "events": [{"phase": "explore", "message": "Running S3-rendezvous.", "scenario_id": "S3-rendezvous"}],
        "errors": [],
    }


def test_investigation_history_exposes_modes_live_configuration_and_preferred_target(tmp_path, monkeypatch):
    monkeypatch.delenv("DURESS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DURESS_LLM_MODEL", raising=False)
    client = _client(tmp_path)

    response = client.get("/investigations")

    assert response.status_code == 200
    assert 'id="new-investigation"' in response.text
    assert 'id="investigation-create-form"' in response.text
    assert 'value="cached"' in response.text
    assert 'value="exhaustive"' in response.text
    assert re.search(r'<option value="razorpay-vulnerable" selected>', response.text)
    assert '<option value="live" disabled>' in response.text
    assert "Live mode unavailable" in response.text
    assert "Committed fixtures are authored responses" in response.text
    assert "page memory only" in response.text


def test_running_investigation_renders_progress_provenance_findings_and_actual_schedule(tmp_path):
    client = _client(tmp_path)
    home = tmp_path / ".duress"
    record = _running_record()
    (home / "investigations" / "inv-run-01.json").write_text(json.dumps(record))
    snapshot = home / "evidence" / "trial-kept" / "snapshot" / "app.db"
    snapshot.parent.mkdir(parents=True)
    snapshot.touch()

    response = client.get("/investigations/inv-run-01")

    assert response.status_code == 200
    html = response.text
    assert 'hx-get="/investigations/inv-run-01/progress"' in html
    assert "Lifecycle · Running" in html
    assert "Business verdict" in html and "Unresolved" in html
    assert "Requested mode" in html and "cached" in html
    assert "Effective mode" in html and "exhaustive" in html
    assert "cache miss" in html and "No matching authored fixture" in html
    assert "authored fixture" in html and "not hosted model inference evidence" in html
    assert "Input tokens" in html and "812 / 12000" in html
    assert "Selected scenarios" in html and "Completed" in html and "Pending" in html
    assert "Hypothesis H1" in html
    assert "src/handler.py" in html and "lines 18–21" in html
    assert "Findings" in html and "One valid scenario trial reproduced" in html
    assert "S3-rendezvous · actual schedule" in html
    assert "concurrent group" in html and "payment.captured" in html
    assert "provider_rendezvous" in html and "2 rendezvous parties" in html
    assert "trial-kept" in html and "/evidence/trial-kept" in html
    assert "Inspect retained state" in html


def test_older_investigation_says_schedule_was_not_retained_and_terminal_run_does_not_poll(tmp_path):
    client = _client(tmp_path)
    record = {
        "record_type": "investigation",
        "investigation_id": "inv-old",
        "target_id": "razorpay-vulnerable",
        "status": "completed",
        "phase": "done",
        "mode_requested": "exhaustive",
        "mode_effective": "exhaustive",
        "verdict": "INCONCLUSIVE",
        "scenarios": [
            {
                "template_id": "S3",
                "scenario_id": "S3-rendezvous",
                "selected_by": "exhaustive",
                "status": "done",
                "result": {"status": "INCONCLUSIVE", "valid": 0, "fail": 0, "inconclusive": 1, "trials": 1},
            }
        ],
    }
    (tmp_path / ".duress" / "investigations" / "inv-old.json").write_text(json.dumps(record))

    response = client.get("/investigations/inv-old")

    assert response.status_code == 200
    assert 'hx-get="/investigations/inv-old/progress"' not in response.text
    assert "Lifecycle · Completed" in response.text
    assert "INCONCLUSIVE" in response.text
    assert "actual normalized schedule was not retained" in response.text
    assert "not reconstructed from the current scenario library" in response.text


def test_investigation_script_covers_start_and_visible_poll_failures(tmp_path):
    response = _client(tmp_path).get("/web/static/investigations.js")

    assert response.status_code == 200
    script = response.text
    assert 'fetch("/api/investigations"' in script
    assert 'response.status !== 202' in script
    assert "window.location.assign(destination)" in script
    assert '"htmx:responseError"' in script
    assert '"htmx:sendError"' in script
    assert "Progress polling has stopped." in script
    assert "retry automatically" in script
    assert "item.loc" in script and "item.msg" in script
    assert "localStorage" not in script and "sessionStorage" not in script
