"""verify-fix decisions, including the cases that need a fake executor.

The truth table itself runs real targets in ``tests/e2e``. These unit tests cover
the branches that cannot be produced reliably from a healthy integration.
"""

from __future__ import annotations

import math

from duress.models import (
    AggregateVerdict,
    FailureSignature,
    ScenarioResult,
    TrialVerdict,
)
from duress.verify import (
    candidate_decision,
    candidate_size,
    control_decision,
    statement,
)

SIGNATURE = FailureSignature(check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"])
OTHER = FailureSignature(check="expected-effect@1", kind="expected_effect_missing", roles=["order:A"])


def result(
    *,
    status: str,
    valid: int,
    fail: int,
    inconclusive: int,
    trials: int,
    signature: FailureSignature | None = None,
    verdicts: list[TrialVerdict] | None = None,
    budget_exhausted: bool = False,
) -> ScenarioResult:
    return ScenarioResult(
        template_id="S3",
        scenario_id="S3",
        params={},
        result=AggregateVerdict(
            status=status,
            valid=valid,
            fail=fail,
            inconclusive=inconclusive,
            trials=trials,
            signature=signature,
            signature_counts={signature.key: fail} if signature else {},
            reasons=(["fail_observed"] if fail else []),
            budget_exhausted=budget_exhausted,
        ),
        verdicts=verdicts or [],
        observations=[],
    )


def fail_trials(signature: FailureSignature, count: int) -> list[TrialVerdict]:
    return [TrialVerdict(status="FAIL", signature=signature) for _ in range(count)]


def pass_trials(count: int) -> list[TrialVerdict]:
    return [TrialVerdict(status="PASS") for _ in range(count)]


def test_candidate_size_uses_the_pre_declared_formula():
    # n_c = max(20, ceil(ln 0.01 / ln(1 - p_a))); a *rare* candidate failure
    # needs more trials, so it is the small p_a that exceeds the cap.
    assert candidate_size(0.5) == max(20, math.ceil(math.log(0.01) / math.log(0.5)))
    assert candidate_size(0.85) == 20
    assert candidate_size(0.9) == 20
    assert candidate_size(0.1) == 44
    assert candidate_size(0.09) is None
    assert candidate_size(0.0) is None


def test_control_insufficient_valid():
    stats = control_decision(
        result(status="PASS", valid=2, fail=0, inconclusive=1, trials=3),
        p0=1.0,
        required_valid=5,
    )
    assert stats["reason"] == "control_insufficient_valid"


def test_control_not_reproduced():
    stats = control_decision(
        result(status="PASS", valid=10, fail=0, inconclusive=0, trials=10),
        p0=1.0,
        required_valid=5,
    )
    assert stats["reason"] == "control_not_reproduced"


def test_control_degraded():
    # 6/10 same-signature failures is below p0/2 = 0.5.
    stats = control_decision(
        result(
            status="FAIL",
            valid=10,
            fail=6,
            inconclusive=0,
            trials=10,
            signature=SIGNATURE,
        ),
        p0=1.0,
        required_valid=5,
    )
    assert stats["p_hat"] == 0.6
    # 0.6 >= 0.5, so this one is acceptable; make it clearly degraded.
    stats = control_decision(
        result(
            status="FAIL",
            valid=10,
            fail=4,
            inconclusive=6,
            trials=10,
            signature=SIGNATURE,
        ),
        p0=1.0,
        required_valid=5,
    )
    assert stats["p_hat"] == 0.4
    assert stats["reason"] == "control_degraded"


def test_candidate_still_reproduces():
    stats = candidate_decision(
        result(
            status="FAIL",
            valid=1,
            fail=1,
            inconclusive=0,
            trials=1,
            signature=SIGNATURE,
            verdicts=fail_trials(SIGNATURE, 1),
        ),
        original=SIGNATURE,
        n_c=20,
        allowance=2,
    )
    assert (stats["outcome"], stats["reason"]) == ("NOT_VERIFIED", "still_reproduces")


def test_candidate_other_violation():
    stats = candidate_decision(
        result(
            status="FAIL",
            valid=1,
            fail=1,
            inconclusive=0,
            trials=1,
            signature=OTHER,
            verdicts=fail_trials(OTHER, 1),
        ),
        original=SIGNATURE,
        n_c=20,
        allowance=2,
    )
    assert (stats["outcome"], stats["reason"]) == ("NOT_VERIFIED", "other_violation")


def test_candidate_insufficient_valid():
    stats = candidate_decision(
        result(status="INCONCLUSIVE", valid=3, fail=0, inconclusive=17, trials=20),
        original=SIGNATURE,
        n_c=20,
        allowance=2,
    )
    assert (stats["outcome"], stats["reason"]) == (
        "INCONCLUSIVE",
        "candidate_insufficient_valid",
    )


def test_candidate_verified_and_statement_is_conditional():
    stats = candidate_decision(
        result(
            status="PASS",
            valid=20,
            fail=0,
            inconclusive=0,
            trials=20,
            verdicts=pass_trials(20),
        ),
        original=SIGNATURE,
        n_c=20,
        allowance=2,
    )
    assert stats["outcome"] == "VERIFIED"
    text = statement(k=5, v=5, n_c=20, p_a=0.99, vulnerable="v", candidate="c")
    assert "5/5 valid control trials on v" in text
    assert "not observed in 20 valid trials on c" in text
    assert "<= 1%" in text
    assert "This does not establish correctness" in text
    assert "0.150" in text  # 3 / 20