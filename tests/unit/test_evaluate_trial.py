"""C.6: one test per precedence step, with the named M1 controls."""

from __future__ import annotations

from duress.models import (
    AttemptRecord,
    BuiltScenario,
    TableSnapshot,
    TrialObservation,
)
from duress.oracle import evaluate_trial
from duress.profiles import load_seed
from duress.scenarios import build_scenario

from .test_invariants import attempt, clean_tables, seed_tables


def scenario(registry, target_id: str, template_id: str) -> tuple:
    profile = registry.get(target_id)
    seed = load_seed(profile)
    built = build_scenario(template_id, {}, profile.roles, seed)
    return profile, seed, built


def observation(
    built: BuiltScenario,
    tables: dict | None = None,
    attempts: list[AttemptRecord] | None = None,
    **kwargs,
) -> TrialObservation:
    data = {
        "trial_id": "t-test",
        "target_id": "razorpay-fixed",
        "scenario_id": built.scenario_id,
        "snapshot": TableSnapshot(tables=tables if tables is not None else clean_tables()),
        "attempts": attempts if attempts is not None else [attempt("d1")],
    }
    data.update(kwargs)
    return TrialObservation(**data)


def verdict(profile, seed, built, obs):
    return evaluate_trial(
        obs,
        schedule=built.schedule,
        expectation=built.expectation,
        roles=profile.roles,
        baseline=TableSnapshot(tables=seed.tables()),
        seed_order_ids={row["id"] for row in seed.orders},
    )


def test_step10_pass(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built))
    assert result.status == "PASS"
    assert result.reasons == ["pass"]


def test_step1_watchdog(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, watchdog_fired=True))
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["harness:watchdog"])


def test_step1_stale_call(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    obs = observation(built)
    obs.stub.stale_calls = 1
    result = verdict(profile, seed, built, obs)
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["harness:isolation"])


def test_step1_instance_mismatch(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, instance_ok=False))
    assert result.reasons == ["harness:instance_mismatch"]


def test_step1_baseline_mismatch(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, baseline_ok=False))
    assert result.reasons == ["harness:baseline_mismatch"]


def test_step1_unconfirmed_aborts(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, settlement="unconfirmed"))
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["harness:unconfirmed"])


def test_step2_target_unavailable(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, target_ready=False))
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["target_unavailable"])


def test_step3_state_unreadable(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, snapshot=None))
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["state_unreadable"])


def test_step3_schema_mismatch(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, schema_ok=False))
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["schema_mismatch"])


def test_step4_independent_violation_fails_even_with_deadline(registry):
    profile, seed, built = scenario(registry, "razorpay-vulnerable", "S1")
    tables = clean_tables()
    tables["fulfilments"].append(
        {
            "id": 2,
            "order_id": "order_A",
            "payment_id": "pay_A1",
            "amount": 5000,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    result = verdict(
        profile,
        seed,
        built,
        observation(built, tables, execution_deadline_exceeded=True),
    )
    assert result.status == "FAIL"
    assert result.signature.check == "INV-1@1"
    assert "independent_violation" in result.reasons


def test_step5_deadline_never_passes(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(
        profile,
        seed,
        built,
        observation(built, execution_deadline_exceeded=True),
    )
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["trial_timeout"])


def test_step6_forced_settlement_is_inconclusive(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, settlement="forced"))
    assert (result.status, result.reasons) == ("INCONCLUSIVE", ["settlement:forced"])


def test_step6_dup_then_hang_fails(registry):
    profile, seed, built = scenario(registry, "razorpay-vulnerable", "S1")
    tables = clean_tables()
    tables["fulfilments"].append(
        {
            "id": 2,
            "order_id": "order_A",
            "payment_id": "pay_A1",
            "amount": 5000,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    result = verdict(profile, seed, built, observation(built, tables, settlement="forced"))
    assert result.status == "FAIL"
    assert result.signature.check == "INV-1@1"


def test_step7_required_evidence(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    result = verdict(profile, seed, built, observation(built, attempts=[]))
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["insufficient_evidence:answered:d1"]


def test_step8_non_independent_violation_fails(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    tables = seed_tables()
    tables["payments"][0]["status"] = "captured"
    tables["orders"][0]["status"] = "fulfilled"
    result = verdict(profile, seed, built, observation(built, tables))
    assert result.status == "FAIL"
    assert any(v.check == "INV-4@1" for v in result.violations)


def test_step8_expected_effect_missing_fails(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    rejected = attempt("d1", status=400, body_status="rejected")
    result = verdict(profile, seed, built, observation(built, seed_tables(), [rejected]))
    assert result.status == "FAIL"
    assert result.signature.check == "expected-effect@1"
    assert result.signature.kind == "expected_effect_missing"


def test_s8_d1_200_is_accepted_without_effect(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S8")
    tables = seed_tables()
    tables["payments"][2]["status"] = "captured"
    tables["orders"][1]["status"] = "fulfilled"
    tables["fulfilments"].append(
        {
            "id": 1,
            "order_id": "order_B",
            "payment_id": "pay_B1",
            "amount": 5000,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    attempts = [attempt("d1"), attempt("d2")]
    result = verdict(profile, seed, built, observation(built, tables, attempts))
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["accepted_without_effect:d1"]


def test_s8_d1_500_is_rejection_unobserved(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S8")
    tables = seed_tables()
    tables["payments"][2]["status"] = "captured"
    tables["orders"][1]["status"] = "fulfilled"
    tables["fulfilments"].append(
        {
            "id": 1,
            "order_id": "order_B",
            "payment_id": "pay_B1",
            "amount": 5000,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    attempts = [attempt("d1", status=500, body_status="error"), attempt("d2")]
    result = verdict(profile, seed, built, observation(built, tables, attempts))
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["rejection_unobserved:d1"]


def test_s8_d2_400_is_control_failed(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S8")
    attempts = [
        attempt("d1", status=401, body_status="rejected"),
        attempt("d2", status=400, body_status="rejected"),
    ]
    result = verdict(profile, seed, built, observation(built, seed_tables(), attempts))
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["control_failed_to_execute:d2"]


def test_s8_clean_passes(registry):
    profile, seed, built = scenario(registry, "razorpay-fixed", "S8")
    tables = seed_tables()
    tables["payments"][2]["status"] = "captured"
    tables["orders"][1]["status"] = "fulfilled"
    tables["fulfilments"].append(
        {
            "id": 1,
            "order_id": "order_B",
            "payment_id": "pay_B1",
            "amount": 5000,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    attempts = [
        attempt("d1", status=401, body_status="rejected"),
        attempt("d2"),
    ]
    result = verdict(profile, seed, built, observation(built, tables, attempts))
    assert result.status == "PASS"


def test_corrupted_forced_stop_copy_is_unreadable(registry, tmp_path):
    """A corrupt WAL copy from a forced stop must not be judged."""
    import sqlite3

    from duress.target import read_tables

    corrupted = tmp_path / "app.db"
    corrupted.write_bytes(b"this is not a sqlite database")
    try:
        read_tables(corrupted)
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("corrupted copy was readable")

    profile, seed, built = scenario(registry, "razorpay-fixed", "S1")
    obs = observation(built, settlement="forced", snapshot=None)
    result = verdict(profile, seed, built, obs)
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["state_unreadable"]
