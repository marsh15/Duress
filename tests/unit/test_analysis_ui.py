from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "duress" / "web" / "templates"
ANALYSIS_JS = ROOT / "src" / "duress" / "web" / "static" / "analysis.js"


def render(template: str, **context) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.get_template(template).render(**context)


def _schedule(ids: list[str]) -> dict:
    return {
        "deliveries": [
            {
                "id": delivery_id,
                "effect_intent": "capture_and_fulfil",
                "event": {
                    "type": "payment.captured",
                    "order_role": "order:A",
                    "payment_role": "payment:A1",
                },
            }
            for delivery_id in ids
        ],
        "steps": [{"deliveries": ids}],
        "faults": [{"kind": "provider_rendezvous", "parties": 2, "timeout_ms": 2000}],
        "delivery_policy": {"max_attempts": 1},
    }


def _reduction(outcome: str = "REDUCED", **overrides) -> dict:
    reduced = outcome == "REDUCED"
    record = {
        "record_type": "reduction",
        "reduction_id": "red-example",
        "status": "completed",
        "target_id": "razorpay-vulnerable",
        "original_dna_id": "dna-original",
        "outcome": outcome,
        "reason": "bounded example reason",
        "claim": "1-minimal under admissible single-delivery removal",
        "primary_dna_id": "dna-reduced" if reduced else "dna-original",
        "reduced_dna_id": "dna-reduced" if reduced else None,
        "preflight": {"ok": True},
        "summary": {"trials": 28},
        "result": {
            "status": "reduced" if reduced else "not_reduced",
            "original_deliveries": ["d1", "d2", "d3"],
            "reduced_deliveries": ["d1", "d3"] if reduced else ["d1", "d2", "d3"],
            "reduced_schedule": _schedule(["d1", "d3"]) if reduced else None,
            "trials": 4,
            "candidates": [
                {
                    "index": 1,
                    "removed": "d2",
                    "remaining": ["d1", "d3"],
                    "admissible": True,
                    "inadmissible_reasons": [],
                    "verdict": "accept",
                    "reasons": [],
                    "trials": 4,
                },
                {
                    "index": 2,
                    "removed": "d3",
                    "remaining": ["d1", "d2"],
                    "admissible": False,
                    "inadmissible_reasons": ["pinned_by_expectation:d3"],
                    "verdict": "inadmissible",
                    "reasons": [],
                    "trials": 0,
                },
            ],
        },
        "reduced_document": (
            {
                "dna_id": "dna-reduced",
                "reproducer": {"schedule": _schedule(["d1", "d3"]), "release_condition": "parties"},
            }
            if reduced
            else None
        ),
        "revalidation": (
            {
                "accepted": True,
                "policy": "replay",
                "trials": 20,
                "valid": 20,
                "fail": 20,
                "same_signature": 20,
                "p": 1.0,
                "release_conditions": {"parties": 20},
                "uniform_release": True,
            }
            if reduced
            else None
        ),
        "black_box": {"trials": 4, "valid": 4, "fail": 0, "rate": "0/4"} if reduced else None,
    }
    record.update(overrides)
    return record


def _original() -> dict:
    return {
        "dna_id": "dna-original",
        "reproducer": {
            "release_condition": "parties",
            "schedule": _schedule(["d1", "d2", "d3"]),
        },
    }


def test_reduction_template_renders_schedules_candidate_reasons_and_revalidation() -> None:
    html = render("reduction.html", record=_reduction(), original=_original(), poll=False)

    assert "Original schedule" in html
    assert "Candidate schedule" in html
    assert "Retained and removed deliveries" in html
    assert "d1 · d3" in html
    assert "d2" in html
    assert "pinned_by_expectation:d3" in html
    assert "Candidate revalidation" in html
    assert "Accepted" in html
    assert "Original schedule without injected faults" in html
    assert "28 total trials" in html
    assert "Open accepted primary DNA" in html
    assert "does not establish global minimality" in html
    assert 'data-status-url="/api/reductions/red-example"' in html


def test_reduction_template_distinguishes_all_backend_outcomes_and_null_artifacts() -> None:
    expected = {
        "REDUCED": "A candidate passed the recorded replay revalidation",
        "NOT_REDUCED": "No delivery was removed",
        "SKIPPED": "Reduction was skipped",
        "REJECTED": "The original DNA was rejected by preflight",
        "INCONCLUSIVE": "The saved evidence did not establish",
    }
    for outcome, phrase in expected.items():
        overrides = {}
        if outcome == "REJECTED":
            overrides = {
                "result": None,
                "reduced_document": None,
                "revalidation": None,
                "primary_dna_id": None,
                "reduced_dna_id": None,
                "preflight": {"ok": False, "reason": "invalid_schedule"},
            }
        html = render(
            "reduction.html",
            record=_reduction(outcome, **overrides),
            original=_original(),
            poll=False,
        )
        assert f"<h2 id=\"outcome-title\">{outcome}</h2>" in html
        assert phrase in html
    rejected = render(
        "reduction.html",
        record=_reduction(
            "REJECTED",
            result=None,
            reduced_document=None,
            revalidation=None,
            primary_dna_id=None,
            reduced_dna_id=None,
            preflight={"ok": False, "reason": "invalid_schedule"},
        ),
        original={},
        poll=False,
    )
    assert "invalid_schedule" in rejected
    assert "No original schedule details were retained" in rejected


def _differential_record() -> dict:
    scenarios = [
        "S3[copies=3,fault=rendezvous,latency_ms=200]",
        "S5[fault=rendezvous,latency_ms=200]",
        "S9",
    ]
    targets = ["razorpay-vulnerable", "razorpay-naive-fix", "razorpay-fixed"]
    cells = [
        {
            "scenario_id": scenarios[0],
            "target_id": targets[0],
            "expected": "FAIL",
            "observed": "FAIL",
            "agrees": True,
            "valid": 5,
            "fail": 5,
            "reasons": [],
        },
        {
            "scenario_id": scenarios[0],
            "target_id": targets[1],
            "expected": "PASS",
            "observed": "INCONCLUSIVE",
            "agrees": None,
            "valid": 3,
            "fail": 1,
            "reasons": ["mixed trial evidence"],
        },
        {
            "scenario_id": scenarios[1],
            "target_id": targets[1],
            "expected": "FAIL",
            "observed": "PASS",
            "agrees": False,
            "valid": 5,
            "fail": 0,
            "reasons": [],
        },
        {
            "scenario_id": scenarios[2],
            "target_id": targets[2],
            "expected": None,
            "observed": "PASS",
            "agrees": None,
            "valid": 5,
            "fail": 0,
            "reasons": [],
        },
    ]
    return {
        "record_type": "differential",
        "differential_id": "diff-example",
        "status": "completed",
        "policy": "explore",
        "targets": targets,
        "scenarios": scenarios,
        "cells": cells,
        "summary": {
            "cells": len(cells),
            "compared": 2,
            "agree": 1,
            "disagree": 1,
            "inconclusive": 1,
            "unexpected_cells": [f"{scenarios[1]} @ {targets[1]}: expected FAIL, observed PASS"],
        },
    }


def test_differential_template_renders_expected_observed_matrix_and_null_agreement() -> None:
    html = render(
        "differential.html",
        record=_differential_record(),
        records=[
            {"differential_id": "diff-running", "status": "running", "targets": ["razorpay-vulnerable"]},
            {"differential_id": "diff-done", "status": "completed", "cells": [{}, {}]},
        ],
        targets=[],
        poll=False,
    )

    assert "Scenario comparison matrix" in html
    assert "S3[copies=3,fault=rendezvous,latency_ms=200]" in html
    assert "razorpay-vulnerable" in html
    assert "Observed</strong> FAIL" in html
    assert "Expected</strong> FAIL" in html
    assert "AGREES" in html
    assert "DISAGREES" in html
    assert html.count("INCONCLUSIVE / UNGRADED") == 2
    assert "mixed trial evidence" in html
    assert "diff-running" in html and "diff-done" in html
    assert "0 cells" in html


def test_differential_creation_uses_explore_default_and_exact_trial_labels() -> None:
    html = render(
        "differential.html",
        record=None,
        records=[{"differential_id": "diff-queued", "status": "queued"}],
        targets=[
            {"target_id": "razorpay-vulnerable", "profile": "reference", "variant": "vulnerable"},
            {"target_id": "razorpay-naive-fix", "profile": "reference", "variant": "naive_fix"},
            {"target_id": "razorpay-fixed", "profile": "reference", "variant": "fixed"},
        ],
        poll=False,
    )

    assert 'id="differential-form"' in html
    assert 'value="explore" selected' in html
    assert "5 valid trials per cell (45 total for the default targets)" in html
    assert "20 valid trials per cell (180 total for the default targets)" in html
    assert 'value="razorpay-vulnerable" checked' in html
    assert 'value="razorpay-naive-fix" checked' in html
    assert 'value="razorpay-fixed" checked' in html
    assert "Access token" in html and "Memory only" in html


def test_dna_template_links_only_retained_representative_snapshot_and_enables_reduction() -> None:
    view = {
        "id": "dna-original",
        "target_id": "razorpay-vulnerable",
        "document": {
            "schema_version": 1,
            "dna_id": "dna-original",
            "reproducer": {"domain": "payments-webhook", "schedule": _schedule(["d1", "d2"])},
        },
        "reproducer": {"domain": "payments-webhook", "control_level": "environment_controlled"},
        "showcase": False,
        "signature": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "roles": ["order:A"]},
        "characterization": {"p0": 0.9, "fail": 9, "valid": 10},
        "intent_counts": {"capture_and_fulfil": 2},
        "schedule": _schedule(["d1", "d2"]),
        "release_condition": "parties",
        "deliveries": _schedule(["d1", "d2"])["deliveries"],
        "violation_rows": [],
        "release_conditions": {"parties": 9},
        "reduction": {"available": False, "original_delivery_count": 2, "original_delivery_ids": ["d1", "d2"]},
        "observations": {"trial_id": "trial-retained", "attempts": []},
    }
    html = render("dna.html", view=view, evidence_ids={"trial-retained"})
    absent = render("dna.html", view=view, evidence_ids=set())

    assert 'id="reduce-dna-form"' in html
    assert "Start bounded reduction" in html
    assert 'data-dna-id="dna-original"' in html
    assert 'href="/evidence/trial-retained">Inspect retained business state</a>' in html
    assert "Inspect retained business state" not in absent

    unaccepted = dict(view)
    unaccepted["reduction"] = {
        "available": True,
        "outcome": "INCONCLUSIVE",
        "claim": "candidate search claim",
        "reduced_dna_id": "dna-candidate",
        "primary_dna_id": "dna-original",
        "reduction_id": "red-candidate",
        "reduced_schedule": _schedule(["d1"]),
        "original_delivery_ids": ["d1", "d2"],
        "reduced_delivery_ids": ["d1"],
        "original_delivery_count": 2,
        "reduced_delivery_count": 1,
    }
    unaccepted_html = render("dna.html", view=unaccepted, evidence_ids=set())
    assert "No accepted smaller primary reproducer is recorded" in unaccepted_html
    assert 'href="/dna/dna-candidate"' not in unaccepted_html
    accepted = dict(unaccepted)
    accepted["reduction"] = {**unaccepted["reduction"], "outcome": "REDUCED", "primary_dna_id": "dna-candidate"}
    accepted_html = render("dna.html", view=accepted, evidence_ids=set())
    assert 'href="/dna/dna-candidate">Open accepted primary DNA' in accepted_html


def test_analysis_script_covers_async_endpoints_without_persisting_tokens() -> None:
    script = ANALYSIS_JS.read_text()

    assert '"/api/differential"' in script
    assert "`/api/dna/${encodeURIComponent(dnaId)}/reduce`" in script
    assert '"/api/reductions/' in script or "`/api/reductions/${encodeURIComponent(record.reduction_id)}`" in script
    assert "window.location.assign(recordUrl)" in script
    assert "window.location.reload()" in script
    assert 'headers.set("X-Duress-Token", token)' in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "Check the request fields:" in script
