from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from duress.web import install_web, render_report


def _investigation(*, status: str = "completed") -> dict:
    return {
        "investigation_id": "run-01",
        "target_id": "razorpay-vulnerable",
        "mode_requested": "cached",
        "mode_effective": "exhaustive",
        "fallback_reason": "cache_miss",
        "status": status,
        "phase": "complete",
        "created_at": "2026-10-26T12:00:00Z",
        "hypotheses": [
            {
                "id": "H1",
                "mechanism": "The handler checks before recording the payment.",
                "citations": [
                    {
                        "path": "src/handler.py",
                        "start_line": 18,
                        "end_line": 21,
                        "quote": "if payment_exists(payment_id): return duplicate",
                    }
                ],
                "predicted_failure": {
                    "check": "INV-1@1",
                    "kind": "duplicate_fulfilment",
                    "role": "order:A",
                },
                "scenarios": [{"template_id": "S3", "params": {"fault": "rendezvous"}}],
                "citation_status": "valid",
                "structure_status": "valid",
                "linkage_status": "valid",
                "outcome": "confirmed",
                "rejections": [],
            }
        ],
        "scenarios": [
            {
                "template_id": "S3",
                "scenario_id": "S3-rendezvous",
                "params": {"fault": "rendezvous"},
                "result": {"status": "FAIL", "valid": 1, "fail": 1, "inconclusive": 0, "trials": 1},
                "verdicts": [
                    {
                        "status": "FAIL",
                        "reasons": ["duplicate_fulfilment"],
                        "signature": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "roles": ["order:A"]},
                    }
                ],
                "observations": [
                    {"trial_id": "trial-001", "duration_s": 1.23, "started_at": "2026-10-26T12:00:01Z"}
                ],
            }
        ],
        "dna_ids": ["dna-001"],
        "events": [
            {"phase": "explore", "message": "S3 reproduced duplicate fulfilment.", "scenario_id": "S3-rendezvous", "trial_id": "trial-001"}
        ],
        "trials": [],
        "explanation": "The registry control reproduced the duplicate effect.",
        "errors": [],
        "showcase": True,
    }


def _m3_investigation() -> dict:
    document = _investigation()
    document["hypotheses"] = [
        {
            "draft": {
                "id": "H1",
                "title": "Duplicate payment fulfilment",
                "mechanism": "The handler checks before recording the payment.",
                "mechanism_kind": "check_then_act_race",
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
                "citation_errors": [],
                "structure": "pass",
                "structure_detail": "check then act race found",
                "linkage": "valid",
                "linkage_errors": [],
                "outcome": "confirmed",
                "outcome_detail": "predicted failure observed",
            },
            "usable": True,
            "scenario_ids": ["S3-rendezvous"],
            "scenario_specs": [],
            "source": "model",
        }
    ]
    document["scenarios"] = [
        {
            "template_id": "S3",
            "scenario_id": "S3-rendezvous",
            "params": {"fault": "rendezvous"},
            "selected_by": "hypothesis",
            "status": "done",
            "result": {"status": "FAIL", "valid": 1, "fail": 1, "inconclusive": 0, "trials": 1},
            "trials": [
                {
                    "trial_id": "m3-trial-001",
                    "status": "FAIL",
                    "reasons": ["duplicate_fulfilment"],
                    "signature": {
                        "check": "INV-1@1",
                        "kind": "duplicate_fulfilment",
                        "roles": ["order:A"],
                    },
                    "settlement": "clean",
                    "released_by": "parties",
                    "duration_s": 1.23,
                }
            ],
        }
    ]
    document["trials"] = []
    return document


def _dna() -> dict:
    original_schedule = {
        "events": [],
        "deliveries": [
            {"id": "d1", "effect_intent": "capture_and_fulfil", "event": {"type": "payment.captured", "order_role": "order:A", "payment_role": "payment:A"}},
            {"id": "d2", "effect_intent": "capture_and_fulfil", "event": {"type": "payment.captured", "order_role": "order:A", "payment_role": "payment:A"}},
            {"id": "d3", "effect_intent": "order_duplicate", "event": {"type": "order.paid", "order_role": "order:A", "payment_role": "payment:B"}},
        ],
        "steps": [{"deliveries": ["d1", "d2"]}, {"deliveries": ["d3"]}],
        "faults": [{"kind": "provider_rendezvous", "parties": 2, "timeout_ms": 2000}],
        "delivery_policy": {"max_attempts": 1},
    }
    reduced_schedule = {
        **original_schedule,
        "deliveries": original_schedule["deliveries"][:2],
        "steps": [{"deliveries": ["d1", "d2"]}],
    }
    return {
        "schema_version": 1,
        "dna_id": "dna-001",
        "reproducer_sha256": "0" * 64,
        "reproducer": {
            "domain": "payments-webhook",
            "target_contract": {"process": {"workers": 1}},
            "failure_signature": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "roles": ["order:A"]},
            "release_condition": "parties",
            "control_level": "environment_controlled",
            "schedule": original_schedule,
        },
        "provenance_scenario": {"template_id": "S3", "params": {"fault": "rendezvous"}},
        "environment": {},
        "characterization": {"p0": 0.9, "valid": 10, "fail": 9, "release_conditions": {"parties": 9, "timeout": 1}},
        "observations": {
            "violation_rows": [{"check": "INV-1@1", "kind": "duplicate_fulfilment", "roles": ["order:A"], "entity_ids": ["order_001"]}],
            "attempts": [],
        },
        "reduction": {
            "claim": "1-minimal w.r.t. admissible single-delivery removal under P.",
            "reduced_document": {"dna_id": "dna-reduced", "reproducer": {"schedule": reduced_schedule}},
        },
        "provenance": {"target_id": "razorpay-vulnerable"},
    }


def _client(tmp_path: Path, *, showcase: bool = True) -> TestClient:
    root = tmp_path
    home = root / ".duress"
    (home / "investigations").mkdir(parents=True)
    (home / "dna").mkdir(parents=True)
    (home / "operations").mkdir(parents=True)
    if showcase:
        (root / "showcase" / "dna").mkdir(parents=True)
        (root / "showcase" / "investigation.json").write_text(json.dumps(_investigation()))
        (root / "showcase" / "dna" / "dna-001.json").write_text(json.dumps(_dna()))
    target = SimpleNamespace(
        target_id="razorpay-vulnerable",
        profile_id="razorpay-ref",
        variant="vulnerable",
        domain="payments-webhook",
        contract_version=1,
        workers=1,
    )
    registry = SimpleNamespace(all=lambda: [target])
    workspace = SimpleNamespace(root=root, home=home, registry=registry)
    app = FastAPI()
    install_web(app, lambda: workspace)
    return TestClient(app)


def test_home_and_recorded_showcase_links(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "Workspace overview" in response.text
    assert 'href="/investigations#new-investigation"' in response.text
    assert "New investigation" in response.text
    assert "Investigation unavailable" not in response.text
    assert "Recorded benchmark" in response.text
    assert "/investigations/run-01" in response.text
    assert "/dna/dna-001" in response.text
    assert "Reference targets" in response.text


def test_local_scripts_and_vendored_htmx_are_served(tmp_path: Path) -> None:
    client = _client(tmp_path)
    site_script = client.get("/web/static/site.js")
    htmx = client.get("/web/static/vendor/htmx.min.js")
    license_file = client.get("/web/static/vendor/HTMX-LICENSE.txt")
    assert site_script.status_code == 200
    assert "X-Duress-Token" in site_script.text
    assert htmx.status_code == 200
    assert len(htmx.content) > 40_000
    assert license_file.status_code == 200
    assert "Zero-Clause BSD" in license_file.text


def test_investigation_page_renders_citations_counts_and_trial_timeline(tmp_path: Path) -> None:
    client = _client(tmp_path, showcase=False)
    home = tmp_path / ".duress"
    (home / "investigations" / "run-01.json").write_text(json.dumps(_investigation()))

    response = client.get("/investigations/run-01")
    assert response.status_code == 200
    assert "Confirmed finding" in response.text
    assert "src/handler.py" in response.text
    assert "lines 18–21" in response.text
    assert "INV-1@1" in response.text
    assert "Scenario results" in response.text
    assert "trial-001" in response.text
    assert "S3 reproduced duplicate fulfilment." in response.text
    assert "Portable Failure DNA" in response.text


def test_m3_investigation_page_renders_nested_hypothesis_and_scenario_trial(tmp_path: Path) -> None:
    client = _client(tmp_path, showcase=False)
    (tmp_path / ".duress" / "investigations" / "run-01.json").write_text(
        json.dumps(_m3_investigation())
    )

    response = client.get("/investigations/run-01")

    assert response.status_code == 200
    assert "Confirmed finding" in response.text
    assert "Citation</dt><dd>valid</dd>" in response.text
    assert "src/handler.py" in response.text
    assert "lines 18–21" in response.text
    assert "m3-trial-001" in response.text
    assert "S3-rendezvous" in response.text
    assert "INV-1@1" in response.text
    assert "duplicate_fulfilment" in response.text


def test_running_investigation_progress_fragment_polls_until_terminal(tmp_path: Path) -> None:
    client = _client(tmp_path, showcase=False)
    (tmp_path / ".duress" / "investigations" / "run-01.json").write_text(
        json.dumps(_investigation(status="running"))
    )

    page = client.get("/investigations/run-01")
    fragment = client.get("/investigations/run-01/progress")
    assert page.status_code == 200
    assert 'hx-trigger="every 2s"' in page.text
    assert fragment.status_code == 200
    assert 'hx-get="/investigations/run-01/progress"' in fragment.text
    assert "Investigation trail" in fragment.text


def test_dna_page_shows_intents_release_violations_and_reduction(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/dna/dna-001")
    assert response.status_code == 200
    assert "capture and fulfil" in response.text
    assert "order duplicate" in response.text
    assert "Release condition" in response.text
    assert "provider rendezvous" in response.text
    assert "INV-1@1" in response.text
    assert "Original schedule" in response.text
    assert "3 deliveries" in response.text
    assert "2 deliveries" in response.text
    assert "Verify a fix" in response.text
    assert 'id="reduce-dna-form"' in response.text
    assert "Start bounded reduction" in response.text
    assert "Reduction unavailable" not in response.text
    assert '<button class="button button-primary" type="submit">Start bounded reduction</button>' in response.text
    assert "Import portable JSON" in response.text
    assert "/dna/dna-001/download" in response.text


def test_pages_cover_targets_history_dna_library_and_saved_operation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    targets = client.get("/targets")
    assert "razorpay-vulnerable" in targets.text
    assert "bind to 127.0.0.1" in targets.text
    history = client.get("/investigations")
    assert history.status_code == 200
    assert "run-01" in history.text or "showcase" in history.text

    library = client.get("/dna")
    assert library.status_code == 200
    assert "Import and replay" in library.text
    assert "dna-001" in library.text

    operation = {
        "operation_id": "op-001",
        "kind": "verify_fix",
        "created_at": "2026-10-26T12:01:00Z",
        "dna_id": "dna-001",
        "payload": {
            "outcome": "VERIFIED",
            "statement": "Historical statement from the operation response.",
            "control": {"valid": 5, "fail": 5, "same_signature_failures": 5},
            "candidate": {"valid": 20, "fail": 0, "required_valid": 20},
        },
    }
    (tmp_path / ".duress" / "operations" / "op-001.json").write_text(json.dumps(operation))
    result = client.get("/operations/op-001")
    assert result.status_code == 200
    assert "VERIFIED" in result.text
    assert "Historical statement from the operation response." in result.text
    assert "<h1>Verified</h1>" in result.text
    assert "same signature failures" in result.text


def test_real_m2_showcase_stays_aggregate_and_historical(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    home = tmp_path / ".duress"
    (home / "investigations").mkdir(parents=True)
    (home / "dna").mkdir(parents=True)
    (home / "operations").mkdir(parents=True)
    target = SimpleNamespace(
        target_id="razorpay-vulnerable",
        profile_id="razorpay-ref",
        variant="vulnerable",
        domain="payments-webhook",
        contract_version=1,
        workers=1,
    )
    workspace = SimpleNamespace(
        root=repository_root,
        home=home,
        registry=SimpleNamespace(all=lambda: [target]),
    )
    app = FastAPI()
    install_web(app, lambda: workspace)
    client = TestClient(app)

    page = client.get("/investigations/showcase-m2")
    assert page.status_code == 200
    assert "10" in page.text and "valid trials" in page.text
    assert "5 / 5" in page.text
    assert "20" in page.text and "valid candidate trials" in page.text
    assert "Historical M2 portability check" in page.text
    assert "No hypotheses were recorded" in page.text
    assert "No individual scenario rows were retained" in page.text
    assert "trial-001" not in page.text
    assert "docs/measurements/m2.json" in page.text
    assert "processed" in page.text
    assert "Business state" in page.text
    assert "created" in page.text
    assert "fulfilled" in page.text
    assert "authorized" in page.text
    assert "captured" in page.text
    assert "No fulfilment rows recorded" in page.text
    assert "order_A" in page.text and "pay_A1" in page.text
    assert "Retained M2 representative SQLite snapshot" in page.text


def test_dna_download_returns_portable_json_attachment(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/dna/dna-001/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    assert response.json()["dna_id"] == "dna-001"


def test_report_helper_returns_standalone_html_from_a_dict(tmp_path: Path) -> None:
    report = render_report(_investigation())
    assert report.startswith("<!doctype html>")
    assert "Static investigation report" in report
    assert "S3 reproduced duplicate fulfilment." in report
    assert "<style>" in report
    assert "/web/static/site.css" not in report


def test_missing_and_unsafe_artifact_ids_are_not_served(tmp_path: Path) -> None:
    client = _client(tmp_path, showcase=False)
    missing_dna = client.get("/dna/unknown")
    missing_investigation = client.get("/investigations/unknown")
    missing_operation = client.get("/operations/unknown")
    for response in (missing_dna, missing_investigation, missing_operation):
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("text/html")
        assert "Return to workbench" in response.text
    assert client.get("/dna/%2e%2e%2fsecret").status_code == 404


def test_malformed_dna_record_does_not_break_library_or_detail(tmp_path: Path) -> None:
    client = _client(tmp_path, showcase=False)
    (tmp_path / ".duress" / "dna" / "bad.json").write_text(
        json.dumps({"dna_id": "bad", "reproducer": {}, "provenance_scenario": "bad"})
    )
    assert client.get("/dna").status_code == 200
    assert client.get("/dna/bad").status_code == 404


def test_displayed_dna_json_matches_portable_download_without_ui_metadata(tmp_path: Path) -> None:
    import re
    from duress.models import DnaDocument

    root = Path(__file__).resolve().parents[2]
    workspace = SimpleNamespace(root=root, home=tmp_path)
    app = FastAPI()
    install_web(app, lambda: workspace)
    http = TestClient(app)
    page = http.get("/dna/e8007dd56adae8ae")
    match = re.search(r'<script id="dna-document" type="application/json">(.*?)</script>', page.text, re.S)
    assert match is not None
    embedded = json.loads(match.group(1))
    downloaded = http.get("/dna/e8007dd56adae8ae/download").json()
    assert embedded == downloaded
    assert "showcase" not in embedded
    DnaDocument.model_validate(embedded)


def test_symlinked_records_and_downloads_are_rejected_without_fallback(tmp_path: Path) -> None:
    client = _client(tmp_path)
    home = tmp_path / ".duress"
    root = tmp_path
    (home / "investigations" / "run-01.json").symlink_to(root / "showcase" / "investigation.json")
    (home / "operations" / "op-link.json").symlink_to(root / "showcase" / "investigation.json")
    (home / "dna" / "dna-link.json").symlink_to(root / "showcase" / "dna" / "dna-001.json")

    assert client.get("/investigations/run-01").status_code == 404
    assert client.get("/operations/op-link").status_code == 404
    assert client.get("/dna/dna-link").status_code == 404
    assert client.get("/dna/dna-link/download").status_code == 404
