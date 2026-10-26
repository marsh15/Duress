"""C.14: every derived total is checked here."""

from __future__ import annotations

from duress import limits


def test_trial_phases_sum_to_the_watchdog():
    assert sum(limits.PHASE_TIMEOUTS_S.values()) == 65.0
    assert limits.TRIAL_WATCHDOG_S == 65.0
    assert limits.DEFAULT_LIMITS.watchdog_s == 65.0


def test_schedule_bounds():
    assert limits.MAX_DELIVERIES == 10
    assert limits.MAX_GROUP_SIZE == 10
    assert limits.MAX_ATTEMPTS_PER_DELIVERY == 3
    assert limits.MAX_ATTEMPT_TIMEOUT_MS == 10_000
    assert limits.MAX_PLANNED_WORST_CASE_MS == 25_000
    assert limits.EXECUTION_DEADLINE_S == 30.0
    assert limits.MAX_PLANNED_WORST_CASE_MS < limits.EXECUTION_DEADLINE_S * 1000


def test_investigation_budget():
    assert limits.EXPLORE_TRIALS == 42
    assert limits.CHARACTERIZE_TRIALS == 22
    assert limits.BLACK_BOX_TRIALS == 11
    assert limits.REDUCTION_TRIALS == 40
    assert limits.REVALIDATION_TRIALS == 11
    assert limits.INVESTIGATION_TRIALS == 126
    assert limits.INVESTIGATION_TRIALS <= limits.INVESTIGATION_TRIAL_CAP == 130


def test_operation_budgets():
    assert limits.REPLAY_TRIALS == 11
    assert limits.REDUCE_TRIALS == 51
    assert limits.VERIFY_FIX_CONTROL_TRIALS + limits.VERIFY_FIX_CANDIDATE_TRIALS == 59
    assert limits.VERIFY_FIX_TRIAL_CAP == 59
    assert limits.HTTP_ATTEMPTS_PER_TRIAL == 30
    assert limits.INVESTIGATION_HTTP_CAP == 3900
    assert limits.VERIFY_FIX_HTTP_CAP == 1770


def test_policies():
    assert limits.POLICIES["explore"].required_valid == 5
    assert limits.POLICIES["explore"].max_attempts == 6
    assert limits.POLICIES["explore"].inconclusive_allowance == 1
    assert limits.POLICIES["characterize"].max_attempts == 11
    assert limits.POLICIES["reduce"].required_valid == 3
    assert limits.POLICIES["replay"].required_valid == 10
    assert limits.POLICIES["matrix"].required_valid == 20
    assert limits.POLICIES["matrix"].max_attempts == 22
    assert limits.POLICIES["matrix"].inconclusive_allowance == 2


def test_verify_policies():
    assert limits.verify_control_policy(0.95).required_valid == 5
    assert limits.verify_control_policy(0.95).max_attempts == 6
    assert limits.verify_control_policy(0.5).required_valid == 10
    assert limits.verify_control_policy(0.5).max_attempts == 11
    candidate = limits.verify_candidate_policy(20)
    assert (candidate.required_valid, candidate.max_attempts) == (20, 22)
    candidate = limits.verify_candidate_policy(44)
    assert (candidate.required_valid, candidate.max_attempts) == (44, 48)


def test_model_and_hosted_limits():
    assert limits.MODEL_CALLS == 3
    assert limits.MODEL_INPUT_TOKENS == 30_000
    assert limits.MODEL_BUNDLE_BYTES == 120_000
    assert limits.HOSTED_CONCURRENCY == 1
    assert (limits.HOSTED_PER_IP_PER_HOUR, limits.HOSTED_PER_DAY) == (3, 30)


def test_limits_override_is_isolated():
    base = limits.DEFAULT_LIMITS
    changed = base.with_phase("prepare", 0.1)
    assert changed.phase("prepare") == 0.1
    assert base.phase("prepare") == 5.0
    assert changed.phase("readiness") == base.phase("readiness")