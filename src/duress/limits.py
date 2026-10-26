"""One limits module (plan C.14, B8).

Every derived total in the plan is computed here, so a test can check the
arithmetic instead of trusting prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import floor

# Per-schedule bounds.
MAX_DELIVERIES = 10
MAX_GROUP_SIZE = 10
MAX_ATTEMPTS_PER_DELIVERY = 3
MAX_ATTEMPT_TIMEOUT_MS = 10_000
MAX_PLANNED_WORST_CASE_MS = 25_000
EXECUTION_DEADLINE_S = 30.0

# Phased trial supervisor: P1..P6 sum to the 65 s watchdog (P4b).
PHASE_TIMEOUTS_S: dict[str, float] = {
    "prepare": 5.0,
    "readiness": 10.0,
    "execute": 30.0,
    "drain": 10.0,
    "kill_confirm": 5.0,
    "evaluate": 5.0,
}
TRIAL_WATCHDOG_S = 65.0
TRIAL_START_MARGIN_S = 65.0

# Aggregate budgets.
EXPLORE_TRIALS = 7 * 6
CHARACTERIZE_TRIALS = 2 * 11
BLACK_BOX_TRIALS = 11
REDUCTION_TRIALS = 10 * 4
REVALIDATION_TRIALS = 11
INVESTIGATION_TRIAL_CAP = 130
INVESTIGATION_TRIALS = (
    EXPLORE_TRIALS
    + CHARACTERIZE_TRIALS
    + BLACK_BOX_TRIALS
    + REDUCTION_TRIALS
    + REVALIDATION_TRIALS
)
VERIFY_FIX_CONTROL_TRIALS = 11
VERIFY_FIX_CANDIDATE_TRIALS = 48
VERIFY_FIX_TRIAL_CAP = VERIFY_FIX_CONTROL_TRIALS + VERIFY_FIX_CANDIDATE_TRIALS
HTTP_ATTEMPTS_PER_TRIAL = 30
INVESTIGATION_HTTP_CAP = INVESTIGATION_TRIAL_CAP * HTTP_ATTEMPTS_PER_TRIAL
VERIFY_FIX_HTTP_CAP = VERIFY_FIX_TRIAL_CAP * HTTP_ATTEMPTS_PER_TRIAL
REPLAY_TRIALS = 11
REDUCE_TRIALS = REDUCTION_TRIALS + REVALIDATION_TRIALS

# Model limits (M3).
MODEL_CALLS = 3
MODEL_INPUT_TOKENS = 30_000
MODEL_BUNDLE_BYTES = 120_000
MODEL_TIMEOUT_S = 60.0

# Hosted public-demo limits.
HOSTED_CONCURRENCY = 1
HOSTED_PER_IP_PER_HOUR = 3
HOSTED_PER_DAY = 30


@dataclass(frozen=True)
class Policy:
    """Trial policy from C.6 aggregation."""

    name: str
    required_valid: int
    max_attempts: int

    @property
    def inconclusive_allowance(self) -> int:
        return max(1, floor(0.1 * self.required_valid))


POLICIES: dict[str, Policy] = {
    "explore": Policy("explore", 5, 6),
    "characterize": Policy("characterize", 10, 11),
    "reduce": Policy("reduce", 3, 4),
    "replay": Policy("replay", 10, 11),
    "matrix": Policy("matrix", 20, 22),
}


def verify_control_policy(p0: float) -> Policy:
    """verify-fix stage 2: r = 5, or 10 when the observed rate is below 0.9."""
    r = 5 if p0 >= 0.9 else 10
    return Policy("verify_control", r, r + max(1, floor(0.1 * r)))


def verify_candidate_policy(n_c: int) -> Policy:
    return Policy("verify_candidate", n_c, n_c + max(1, floor(0.1 * n_c)))


@dataclass(frozen=True)
class Limits:
    """Injectable limits.

    Tests override specific phases (e.g. a 0.1 s prepare timeout) without
    changing any production default.
    """

    execution_deadline_s: float = EXECUTION_DEADLINE_S
    watchdog_s: float = TRIAL_WATCHDOG_S
    planned_worst_case_ms: int = MAX_PLANNED_WORST_CASE_MS
    trial_start_margin_s: float = TRIAL_START_MARGIN_S
    quiesce_s: float = 2.0
    phase_timeouts: dict[str, float] = field(
        default_factory=lambda: dict(PHASE_TIMEOUTS_S)
    )

    def phase(self, name: str) -> float:
        try:
            return self.phase_timeouts[name]
        except KeyError:
            raise KeyError(f"unknown trial phase {name!r}") from None

    def with_phase(self, name: str, seconds: float) -> "Limits":
        phases = dict(self.phase_timeouts)
        phases[name] = seconds
        return replace(self, phase_timeouts=phases)


DEFAULT_LIMITS = Limits()


def _check_totals() -> None:
    if sum(PHASE_TIMEOUTS_S.values()) + 0.0 != TRIAL_WATCHDOG_S:
        raise AssertionError("trial phase timeouts do not sum to the watchdog")
    if INVESTIGATION_TRIALS > INVESTIGATION_TRIAL_CAP:
        raise AssertionError("investigation budget exceeds its cap")


_check_totals()