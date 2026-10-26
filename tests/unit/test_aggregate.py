"""C.6 aggregation: FAIL precedence, PASS quota and the inconclusive allowance."""

from __future__ import annotations

from duress.aggregation import aggregate
from duress.limits import POLICIES
from duress.models import FailureSignature, TrialVerdict


def verdict(status: str, kind: str = "duplicate_fulfilment") -> TrialVerdict:
    if status == "FAIL":
        return TrialVerdict(
            status="FAIL",
            signature=FailureSignature(check="INV-1@1", kind=kind, roles=["order:A"]),
        )
    return TrialVerdict(status=status)


def test_fail_wins_over_inconclusive_trials():
    result = aggregate(
        [verdict("FAIL"), verdict("INCONCLUSIVE"), verdict("PASS")], POLICIES["explore"]
    )
    assert result.status == "FAIL"
    assert result.fail == 1
    assert result.valid == 2
    assert result.signature.key == "INV-1@1:duplicate_fulfilment:order:A"


def test_pass_needs_quota_and_allowance():
    result = aggregate(
        [verdict("PASS") for _ in range(5)] + [verdict("INCONCLUSIVE")],
        POLICIES["explore"],
    )
    assert result.status == "PASS"
    assert (result.valid, result.inconclusive) == (5, 1)


def test_too_many_inconclusive_is_inconclusive():
    result = aggregate(
        [verdict("PASS") for _ in range(5)] + [verdict("INCONCLUSIVE") for _ in range(2)],
        POLICIES["explore"],
    )
    assert result.status == "INCONCLUSIVE"
    assert "too_many_inconclusive" in result.reasons


def test_insufficient_valid_and_budget_exhausted():
    result = aggregate([verdict("PASS") for _ in range(4)], POLICIES["explore"], budget_exhausted=True)
    assert result.status == "INCONCLUSIVE"
    assert "insufficient_valid" in result.reasons
    assert result.budget_exhausted is True
    assert "budget_exhausted" in result.reasons


def test_fail_keeps_the_budget_reason():
    result = aggregate([verdict("FAIL")], POLICIES["explore"], budget_exhausted=True)
    assert result.status == "FAIL"
    assert "budget_exhausted" in result.reasons


def test_matrix_quota_is_twenty():
    result = aggregate([verdict("PASS") for _ in range(19)], POLICIES["matrix"])
    assert result.status == "INCONCLUSIVE"
    result = aggregate([verdict("PASS") for _ in range(20)], POLICIES["matrix"])
    assert result.status == "PASS"


def test_signature_counts_and_primary():
    first = TrialVerdict(
        status="FAIL",
        signature=FailureSignature(check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"]),
    )
    second = TrialVerdict(
        status="FAIL",
        signature=FailureSignature(check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"]),
    )
    third = TrialVerdict(
        status="FAIL",
        signature=FailureSignature(check="INV-6@1", kind="unauthorized_mutation", roles=["order:A"]),
    )
    result = aggregate([first, second, third], POLICIES["explore"])
    assert result.status == "FAIL"
    assert result.signature_counts["INV-1@1:duplicate_fulfilment:order:A"] == 2
    assert result.signature.key.startswith("INV-1@1")