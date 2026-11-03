# INVALID trial diagnosis: `harness:target_unavailable` in `inv-20261027T134459-5dffbe`

Scope: the public Free-tier cached investigation recorded in `docs/release/2026-10-27-public/`. It used 31 attempts: 30 valid and one INCONCLUSIVE attempt with reason `harness:target_unavailable` ([README](../release/2026-10-27-public/README.md), line 11).

**Conclusion.** The invalid attempt was a readiness-budget expiry: the freshly spawned target process was still alive after the 10 s readiness budget without answering `/healthz` with the expected instance. The most likely cause is a slow cold start on the Free instance, which has roughly 12x the per-trial cost of a local run. The retained evidence does not prove this (no per-phase timing or target log was kept). The verdict was not affected. The harness classified the attempt correctly, so no source change was made. The regression test covers the failure mode.

## What was retained

- `docs/release/2026-10-27-public/investigation.json`, `scenarios[0]` (S1): six trials, five PASS and one INCONCLUSIVE. The INCONCLUSIVE one is `t7fff6ab0295f`, reasons `["harness:target_unavailable"]`, `duration_s` 11.9, settlement `clean`. Trial cap 130, trials used 31, no `errors`.
- S1 met its quota with 5 valid trials, so the attempt was excluded and retried inside the existing budget. The S3 and S5 FAIL verdicts (5/5 valid each) carry the verdict.
- Per-trial rows are built by `_trial_rows` at `src/duress/orchestrator.py:278-295`. That function keeps status, reasons, settlement, released_by and duration. It drops `phase_timings`, `harness_errors` detail and `exit_code`, so the retained record cannot show which phase consumed the time.

## Why the reason means readiness timed out

- `src/duress/target.py:241-244`: `started = await self._timed("readiness", self._start())`. Only a `TIMEOUT` result sets `harness_errors.append("target_unavailable")` at line 244.
- The other "not ready" exits return `False` without a harness error: the process exiting early (`target.py:347-348`) and the deadline loop ending (`target.py:368`). They produce a bare `target_unavailable` with no `harness:` prefix (`src/duress/oracle.py:81-82`, while `_harness_reason` at `oracle.py:27-39` only prefixes recorded harness errors).
- The record shows the prefixed form, so the timeout path ran: the process was alive for the whole readiness window.
- Excluded by the same reasoning: `instance_mismatch` (`target.py:365`, which would give a different reason) and a bind failure caused by port reuse. `free_port()` (`src/duress/provider_stub.py:22-28`) has a real bind-then-close race, but a target that failed to bind would exit and produce the bare reason, not the prefixed one.

## Budget

- `src/duress/limits.py:23`: `"readiness": 10.0`.
- `src/duress/limits.py:29`: `TRIAL_WATCHDOG_S = 65.0`. `_check_totals` at `limits.py:131-135` requires the phases to sum to the watchdog.
- The readiness clock starts with the outer `_timed` (`target.py:530-535`, before the subprocess spawns) and again inside `_start` at `target.py:344`. The outer timer always fires first, so the inner deadline never decides the outcome. This is redundant but not wrong.
- Each trial spawns a fresh `init-db` subprocess (`_prepare`, `target.py:262-301`) and a fresh `serve` process (`_start`, `target.py:303-368`). Readiness is therefore a full interpreter, import and bind cost paid every trial.
- The target `/healthz` handler is constant (`targets/razorpay_ref/vulnerable/handler.py:142-144`). The cost is process start, not the handler.

## Evidence on speed

- Retained trial durations: 21 scenario trials in the record, all between 9.7 s and 13.2 s (median 10.5 s). The failed attempt, at 11.9 s, is not an outlier in total time. The 10 characterization trials are not itemised in the record.
- Local, same repository, this Mac (arm64): a full S1 trial with the real supervisor took 0.81 to 0.85 s total, with readiness 0.44 to 0.45 s (temporary probe, removed). A standalone cold start from `init-db` plus `serve` until `/healthz` answered 200 took 0.46 to 0.97 s over three runs.
- Inference, not a measurement: the Free instance is about 12x slower end to end. If that ratio applied to readiness, a typical start would take roughly 5 s, leaving about 2x headroom to the 10 s budget, so a tail start could cross the limit. The retained evidence cannot confirm this.

## Alternatives considered

- Port reuse: discussed above; not supported by the reason string.
- Stale process from an earlier trial: each trial owns its process group, and the failed trial's settlement is `clean`. Not supported.
- Harness event loop blocked during the window: readiness only awaits (`httpx` and `asyncio.sleep`, `target.py:344-368`). Not supported.
- Target bug (hung startup, not just slow): not excluded. The target log would distinguish a slow start from a hang, and it was not retained (`target.log` lives in the trial workdir under the ephemeral `/tmp/duress`, per the README).
- Unrelated: the CI PostgreSQL readiness race in `docs/release/2026-10-27-public/ci-readiness-recovery.md` is a different component.

## Why no source change was made

- `src/duress/target.py` and `src/duress/limits.py` are pinned in `docs/productization/core-freeze.json`, and their current hashes match the pins. A change to either file would break the freeze, and the task forbids changing core semantics or the freeze.
- The behaviour is correct as specified: a harness fault is INCONCLUSIVE, never a FAIL, and it was retried within budget (`limits.py:76-81`, explore policy 5 valid of 6 attempts, one inconclusive allowance). The verdict stands.
- Freeze status at HEAD, for the record: 46 files checked, 42 match, and 4 already mismatch before this work (`src/duress/cli.py`, `src/duress/external/reporting.py`, `src/duress/external/web.py`, `src/duress/web/__init__.py`). These were not touched.

## Regression test

`tests/unit/test_readiness_slow_start.py` (new):

- `test_slow_start_past_readiness_budget_is_inconclusive`: real `razorpay-fixed` target, serve command delayed 2.0 s, readiness budget 0.5 s. Asserts `target_ready` is False, `harness_errors == ["target_unavailable"]`, the process group is gone, and the verdict is `INCONCLUSIVE` with reason `harness:target_unavailable`. This reproduces the record's reason on the same code path.
- `test_same_slow_start_inside_budget_runs_the_schedule`: the same kind of delay (0.3 s) under the default budget. Asserts the target becomes ready, no `target_unavailable`, and the schedule reaches the target. This shows the verdict depends only on the budget, not on the target.

Limits of the test: the delay is simulated, not the Free-tier speed. It reproduces the failure mode, not the rate.

## Mitigation (proposed, not applied)

1. Measure before changing anything: keep `phase_timings` (already produced at `target.py:223-224`) in the per-trial record (`orchestrator.py:278-295`), and keep the target log for INCONCLUSIVE trials. Run one Free investigation to get the real readiness distribution. Both files are frozen, so this needs explicit owner approval.
2. If readiness tails approach 10 s: raise the readiness budget through a deployment setting. `limits.py:131-135` requires the watchdog to be raised to match, and `TRIAL_START_MARGIN_S` and the 440 s wall cap (`wall_cap_s`) need re-checking. A larger budget costs time only on slow starts, but the worst case (every start hitting the new budget) must still fit the 440 s wall cap, so size the change from measured readiness. This changes harness-invalid rates, not verdict rules.
3. Do not weaken the classification: a readiness timeout must stay INCONCLUSIVE and must never become PASS or FAIL.

## Uncertainty, stated plainly

- Whether the failed start was slow or hung is not established. No target log, per-phase timing or Free-instance readiness measurement exists in the retained evidence.
- The 12x Free-versus-local factor comes from total trial durations, not readiness alone, and the readiness share of the Free timing is inferred.
- The retained trial record cannot show which phase consumed the time, so this diagnosis rests on the reason-string path and on local timing.
