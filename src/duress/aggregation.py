"""Aggregation policy (plan C.6)."""

from __future__ import annotations

from .limits import Policy
from .models import AggregateVerdict, FailureSignature, TrialVerdict


def aggregate(
    verdicts: list[TrialVerdict],
    policy: Policy,
    *,
    budget_exhausted: bool = False,
) -> AggregateVerdict:
    fails = [verdict for verdict in verdicts if verdict.status == "FAIL"]
    passes = [verdict for verdict in verdicts if verdict.status == "PASS"]
    inconclusive = [verdict for verdict in verdicts if verdict.status == "INCONCLUSIVE"]
    valid = len(fails) + len(passes)

    signature_counts: dict[str, int] = {}
    for verdict in fails:
        if verdict.signature:
            signature_counts[verdict.signature.key] = (
                signature_counts.get(verdict.signature.key, 0) + 1
            )
    primary: FailureSignature | None = fails[0].signature if fails else None
    if signature_counts:
        primary_key = max(signature_counts, key=lambda key: signature_counts[key])
        primary = next(
            (
                verdict.signature
                for verdict in fails
                if verdict.signature and verdict.signature.key == primary_key
            ),
            primary,
        )

    if fails:
        reasons = ["fail_observed", f"signature:{primary.key}" if primary else "signature:unknown"]
        if budget_exhausted:
            reasons.append("budget_exhausted")
        return AggregateVerdict(
            status="FAIL",
            valid=valid,
            fail=len(fails),
            inconclusive=len(inconclusive),
            trials=len(verdicts),
            signature=primary,
            signature_counts=signature_counts,
            reasons=reasons,
            budget_exhausted=budget_exhausted,
        )

    allowance = policy.inconclusive_allowance
    if valid >= policy.required_valid and len(inconclusive) <= allowance:
        return AggregateVerdict(
            status="PASS",
            valid=valid,
            fail=0,
            inconclusive=len(inconclusive),
            trials=len(verdicts),
            reasons=["quota_met"],
            budget_exhausted=budget_exhausted,
        )

    reasons: list[str] = []
    if budget_exhausted:
        reasons.append("budget_exhausted")
    if valid < policy.required_valid:
        reasons.append("insufficient_valid")
    if len(inconclusive) > allowance:
        reasons.append("too_many_inconclusive")
    if not reasons:
        reasons.append("inconclusive")
    return AggregateVerdict(
        status="INCONCLUSIVE",
        valid=valid,
        fail=0,
        inconclusive=len(inconclusive),
        trials=len(verdicts),
        reasons=reasons,
        budget_exhausted=budget_exhausted,
    )