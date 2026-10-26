# Duress — Evidence

The M0–M2 sections were generated from retained measurements by the milestone evidence scripts. The web release section reports observed commands and links their JSON/JUnit records. No result is projected. The M0 gate is E1, E2 and E3; E4–E6 are measurements, not pass criteria.

## M1 — engine, oracle and isolation

Command `uv run pytest -q -m "not slow"`: exit 0, 147 passed, 0 failed, 3 deselected in 187.53 s. M1 acceptance files collected 115 tests.

In the container (`duress:m1`, `python:3.12-slim`): exit 0, 147 passed, 0 failed in 182.53 s.

| Area | Acceptance evidence |
|---|---|
| exec@1 runner | one conformance test per clause C1–C12 |
| phased supervisor | P1/P2/P4/P6 timeout outcomes, watchdog, kill-confirm |
| deadline precedence | DL-1 trial_timeout, DL-2 FAIL with the flag, DL-3 no flag on planned timeouts |
| oracle | one test per C.6 step, including dup_then_hang and unconfirmed |
| invariant pack | one violating and one clean fixture per member, plus MP-1/MP-2/MP-3 |
| isolation | spawn_orphan, cancellation cleanup, pidfile sweep, stale call, wrong instance, loopback, A8 |
| matrix | vulnerable S3 FAIL; fixed S1/S2/S3/S8 PASS; vulnerable S1/S2/S8 PASS |
| controls | no_sig_check INV-6, ack_without_effect INV-5, reject_all expected effect, crash_on_boot INCONCLUSIVE |
| M0 agreement | 20 engine trials agree with the independent E1 counts |
| E8 | fixed, 2 workers, copies 5: 20/20 valid PASS |
| F1 launch command | the supervisor runs the profile's declared serve command; exit 0 and clean settlement |

Historical scope: this M1 run preceded M2. S5/S6/S9, DNA, replay and verify-fix were subsequently implemented and verified in the M2 section below. Autonomous investigation remains pending. The fixture-mode target is a control, not a realistic integration. PASS and FAIL are bounded empirical statements about the trials that ran.

## M2 — DNA, replay, verify-fix and the API

Command `uv run pytest -q tests/unit/test_verify_decisions.py tests/integration/test_matrix_m2.py tests/integration/test_replay_preflight.py tests/integration/test_dna_journey.py tests/e2e/test_verify_truth_table.py tests/integration/test_api.py`: exit 0, 51 passed, 0 failed in 161.04 s.

| Artifact field | Observed value |
|---|---|
| dna_id | e8007dd56adae8ae |
| failure signature | {'check': 'INV-1@1', 'kind': 'duplicate_fulfilment', 'roles': ['order:A']} |
| p0 (valid trials) | 1.0 (10 valid) |
| release condition | parties |
| control level | environment_controlled |
| embedded invariant members | 6 |

Portability (`scripts/portability.sh`): exit 0; build in home A, copy only the artifact into home B, replay -> REPRODUCED, verify-fix -> VERIFIED.

Container (`duress:m2`, public service on 0.0.0.0:8000): health ok, replay -> REPRODUCED, verify-fix -> VERIFIED, dna e8007dd56adae8ae.

M2 acceptance files cover: S5 two-event race, S6 timeout-then-retry, S9 second payment after fulfilment (MP-1, 20 valid trials), MP-2 (`mark_duplicate_captured` -> INV-4), the five-row verify-fix truth table, every preflight rejection, a different valid secret still reproducing, and API authorization/admission control.

## Environment

| Component | Local | Docker |
|---|---|---|
| fastapi | 0.162.0 | 0.162.0 |
| httpx | 0.28.1 | 0.28.1 |
| platform | macOS-27.0-arm64-arm-64bit | Linux-7.2.9-orbstack-00391-g2b298d05fbd4-aarch64-with-glibc2.41 |
| python | 3.12.14 | 3.12.15 |
| sqlite | 3.53.1 | 3.46.1 |
| uvicorn | 0.54.0 | 0.54.0 |

## Acceptance gate

| Run | Status | Gate tests collected | Wall time | Recorded at |
|---|---|---|---|---|
| local | pass | 4 | 74 s | 2026-10-26T12:58:36+00:00 |
| docker | pass | 4 | 81 s | 2026-10-26T13:05:02+00:00 |

Gate suites: `targets/razorpay_ref/tests` (both variants), `tests/m0/test_e1_vulnerable_race.py`, `tests/m0/test_e2_fixed_exactly_once.py`, `tests/m0/test_e3_drain.py`. The script fails if collection is not exactly 4.

## E1 — vulnerable two-party race (gate)

Variant `vulnerable`, 2 concurrent duplicate deliveries, rendezvous parties=2. Attempts: 20; valid: 20; invalid: 0.

| Metric | Result | Gate |
|---|---|---|
| Two fulfilments + party-based release | 20/20 valid trials | ≥ 19/20 |
| Release conditions | {'parties': 20} | parties |
| Both duplicate rows linked to the expected payment | yes (pay_A1) | yes |
| Fulfilment counts | [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2] | 2 per trial |

Each valid trial required instance-matched readiness, both attempts answered, exit 0 within 10 s, and the process group confirmed gone. Counts come from direct SQL on a copy taken after confirmed exit.

## E2 — corrected exactly-once (gate)

| Copies/parties | Attempts | Valid | Invalid | Exactly once | All checks |
|---|---|---|---|---|---|
| 2/2 | 20 | 20 | 0 | 20/20 | yes |
| 5/5 | 20 | 20 | 0 | 20/20 | yes |

Per valid trial: exactly one fulfilment linked to the payment; payment locally `captured`; order `fulfilled`; `released_by=parties`; all responses 2xx; exactly one `processed`; the rest `duplicate`; zero 5xx.

## E3 — process settlement (gate)

SIGTERM 0.5 s after dispatch, provider latency 2000 ms. Attempts: 10; valid: 10; clean settlements: 10/10.

| Check | Result |
|---|---|
| Exit 0 within 10 s | yes |
| Process group confirmed gone | yes |
| Fulfilment committed | yes |
| Drain seconds (all trials) | [0.182, 0.184, 0.185, 0.182, 0.125, 0.173, 0.131, 0.18, 0.125, 0.132] |

## E4 — client disconnect (measured, not a gate)

Attempt timeout 0.3 s, provider latency 1000 ms, one delivery. Valid trials: 10/10. Client timed out in 10; effect committed in 10 (10/10).

This measures assumption A9: the handler is not cancelled by the client disconnect, so the effect is committed before the drain completes. It is an observation about this target, not a general claim.

## E5 — reproduction rates (measured, not a gate)

| Condition | Control level | Valid | Double fulfilment | Counts |
|---|---|---|---|---|
| black_box | black_box | 20 | 20/20 | [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2] |
| latency_50 | environment_controlled | 20 | 20/20 | [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2] |
| latency_200 | environment_controlled | 20 | 20/20 | [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2] |

Black-box means no provider fault; concurrency comes only from the delivery group. Latency runs are `environment_controlled` (plan C.3). The observed black-box rate is high because the vulnerable handler awaits the provider between its check and its write, which widens the window even at zero injected latency; this is a property of the reference defect, not a general prediction.

## E6 — phase timings (measured, not a gate)

Fixed target, two-party group, 10/10 valid trials. Total p50 0.7383 s, p95 0.8768 s, max 0.8768 s.

| Phase | p50 (s) | p95 (s) | max (s) |
|---|---|---|---|
| prepare | 0.0565 | 0.0595 | 0.0595 |
| readiness | 0.4017 | 0.4492 | 0.4492 |
| execute | 0.0447 | 0.0583 | 0.0583 |
| drain | 0.2417 | 0.309 | 0.309 |
| kill_confirm | 0.0 | 0.0 | 0.0 |
| evaluate | 0.0041 | 0.0044 | 0.0044 |

Phase bounds from C.2.2 are 5/10/30/10/5/5 s. M0 does not yet enforce them; the timings are the baseline for the M1 supervisor.

## Limitations

- **Exit-status wrapper.** uvicorn re-raises the captured signal after a graceful shutdown (exit -15). Target contract C.1 requires exit 0, so the reference target launches through `razorpay_ref.serve`, which keeps uvicorn's drain and suppresses the re-raise. This is target-side, not harness-side.
- **Controlled reproduction.** E1 and E2 are environment-controlled by a party rendezvous. E5 measures unrestricted black-box bursts separately; the two must not be conflated.
- **Historical M0 scope.** M0 verification uses direct SQL on a post-exit copy. The oracle, invariant pack and DNA were subsequently implemented in M1–M2; the web release is described separately.
- **Provider state.** The stub's fixtures are external truth and are never mutated; all counted effects are the target's local rows.
- **Container gate.** E7 runs the same gate files in Docker; the measured suites are not run in the container.

## Web application — verification scope

The frontend release preserves the M0–M2 targets and trusted execution services. The pre-change `uv run pytest -q -m 'not slow'` run at `d08409c` completed with **198 passed, 3 deselected** in 351.59 s. This was rerun, not assumed from the previous milestone.

The focused web, records, measurement and read-only API suite passed **26 tests** locally. `DURESS_BROWSER_TESTS=1 uv run pytest -q tests/browser` (executed through `scripts/measure.py`) passed **7 tests** in 59.28 s. The browser tests use Chromium against a temporary local application home, not mocked execution endpoints. They include actual DNA export, REPRODUCED replay and VERIFIED fix verification, plus saved result pages, tamper rejection, navigation, 390/768 px layouts, keyboard focus and HTML escaping. axe-core 4.10.3 found **zero serious or critical violations** on the workspace, recorded investigation and DNA pages. A separate full axe inspection returned no violations on those pages.

Raw final browser evidence: `docs/measurements/release-20261026T143734Z-0d965b83.json` and its referenced JUnit XML. An earlier browser run had 6 passes and 1 failure due to an incorrect label expectation (`Recorded benchmark` versus `Recorded M2 benchmark`); its failed record is retained rather than presented as a passing gate. Earlier exploratory checks also found insufficient muted-text contrast and missing HTML 404 pages; those were corrected before the final browser gate.

Docker build `docker build -t duress:web .` succeeded. The container was started with `PORT=8080`, exposed on local port 18765, and its log reported `0.0.0.0:8080`. `scripts/smoke.sh http://127.0.0.1:18765 --dna showcase/dna/e8007dd56adae8ae.json` passed health, targets and page checks, then observed **REPRODUCED** and **VERIFIED** through the existing API. The same focused suite passed **26 tests** in the Linux container. `duress sweep` returned `nothing to sweep`. Commands and retained outputs are in `docs/measurements/web-container.json`.

Screenshots under `docs/screenshots/` were captured from the running local application at 1440 px width. They show the recorded showcase, not an autonomous live investigation. The showcase artifact is the original M2 DNA; business-state rows were read from its retained representative SQLite snapshot without running another experiment.

**Not verified:** a Render deployment, hosted/browser smoke tests, hosted timing or capacity, and a live model provider. Autonomous investigation and reduction are pending and were excluded from this frontend assignment. The full M6 milestone is not claimed.

### Broad regression observation and final artifact presentation check

The first post-change fast run returned **222 passed, 2 failed, 7 skipped, 3 deselected** in 519.07 s. The failures were in unchanged tests: `test_s9_fixed_second_payment_is_an_order_duplicate` reached the 20-valid-trial aggregate PASS quota but then asserted one fulfilment on every observation, including an empty snapshot; `test_c10_rendezvous_releases_by_parties_then_timeout_then_not_at_all` observed about 78 ms against its 50 ms assertion. This run overlapped browser and Docker work. That overlap is a possible contributing condition, not an established cause. No engine, target or original assertion was modified.

Both failed tests passed unchanged in a focused rerun (**2 passed**), recorded in `docs/measurements/release-20261026T144731Z-b6bd12c0.json`. The failed broad run remains in `release-20261026T143735Z-9431df52.json` and its JUnit. Failure traceback trailing spaces were normalized for Git; outcomes and assertion text were retained. A serial broad rerun was then started to assess the complete gate.

Final presentation inspection found that a UI-only showcase flag was being added to the displayed DNA dictionary. The flag now belongs to the view context. The added regression verifies that embedded JSON equals the downloadable artifact and validates as `DnaDocument`. The focused suite after this fix passed **27 tests**. This does not alter DNA integrity checking or execution semantics.

### Final serial regression gate

`DURESS_RECORD_MEASUREMENTS=1 uv run python scripts/measure.py --run fast` completed successfully: **225 passed, 0 failed, 7 browser checks skipped** in 383.21 s. The skipped browser checks were executed separately in the passing browser gate above. The run used commit `4743a5e` application code; its source-tree digest and environment are retained in `docs/measurements/release-20261026T144814Z-2f711a0e.json`, with JUnit at the referenced path. No assertion or backend code changed between the failed broad run, the focused diagnosis and this serial rerun, apart from the separately tested frontend artifact-presentation correction.

The earlier failures demonstrate sensitivity worth investigating separately in the existing backend test harness. A passing rerun does not erase them or prove their cause. This frontend release leaves the engine and those tests unchanged.

### Clean checkout and final container

A fresh clone of GitHub commit `4743a5e` was clean before and after `uv sync --frozen` and the focused page/records/measurement/API suite: **27 passed**, using the clean environment's Python **3.14.7**. This is focused frontend compatibility evidence, not a full engine regression on Python 3.14. The full serial gate above used Python 3.12.14.

Rebuilt the final application as `duress:web-final`, including the artifact-display correction. Its focused suite passed **27 tests** on Linux/Python 3.12.15. Its public pages, portable payload replay (**REPRODUCED**) and fix verification (**VERIFIED**) passed again with `PORT=8080`. Cleanup returned `nothing to sweep`. Exact commands, image identity, outputs and JUnit references are in `docs/measurements/web-final.json`.

The frontend checkpoint `4743a5e` was pushed and confirmed on GitHub. The trusted execution files and target implementations remain unchanged from `d08409c`. All README local links were checked to resolve. No hosted deployment, autonomous model execution or reduction algorithm is claimed by these checks.

## M6 — Release measurements

The rows below come from saved run records and their JUnit XML. Missing runs are not inferred.

### Run `20261026T143205Z-44474d7a`

Observed at: `2026-10-26T14:32:07+00:00`. Environment: Python `3.12.14`, SQLite `3.53.1`, `macOS-27.0-arm64-arm-64bit`.

| Suite | Command | Exit | Passed | Failed | Errors | Skipped | Seconds | Raw JUnit |
|---|---|---|---|---|---|---|---|---|
| pytest | `/opt/homebrew/bin/uv run pytest tests/unit/test_records.py tests/unit/test_measure.py tests/integration/test_web_api.py --junitxml=/Users/santoshkumar/myprojects/Duress/docs/measurements/release-runs/20261026T143205Z-44474d7a/01-pytest/junit.xml` | 0 | 14 | 0 | 0 | 0 | 1.14 | `docs/measurements/release-runs/20261026T143205Z-44474d7a/01-pytest/junit.xml` |
### Run `20261026T143459Z-2952c5f6`

Observed at: `2026-10-26T14:36:05+00:00`. Environment: Python `3.12.14`, SQLite `3.53.1`, `macOS-27.0-arm64-arm-64bit`.

| Suite | Command | Exit | Passed | Failed | Errors | Skipped | Seconds | Raw JUnit |
|---|---|---|---|---|---|---|---|---|
| pytest | `/opt/homebrew/bin/uv run pytest tests/browser -q --junitxml=/Users/santoshkumar/myprojects/Duress/docs/measurements/release-runs/20261026T143459Z-2952c5f6/01-pytest/junit.xml` | 1 | 6 | 1 | 0 | 0 | 66.41 | `docs/measurements/release-runs/20261026T143459Z-2952c5f6/01-pytest/junit.xml` |
### Run `20261026T143734Z-0d965b83`

Observed at: `2026-10-26T14:38:34+00:00`. Environment: Python `3.12.14`, SQLite `3.53.1`, `macOS-27.0-arm64-arm-64bit`.

| Suite | Command | Exit | Passed | Failed | Errors | Skipped | Seconds | Raw JUnit |
|---|---|---|---|---|---|---|---|---|
| pytest | `/opt/homebrew/bin/uv run pytest tests/browser -q --junitxml=/Users/santoshkumar/myprojects/Duress/docs/measurements/release-runs/20261026T143734Z-0d965b83/01-pytest/junit.xml` | 0 | 7 | 0 | 0 | 0 | 59.28 | `docs/measurements/release-runs/20261026T143734Z-0d965b83/01-pytest/junit.xml` |
### Run `20261026T143735Z-9431df52`

Observed at: `2026-10-26T14:46:24+00:00`. Environment: Python `3.12.14`, SQLite `3.53.1`, `macOS-27.0-arm64-arm-64bit`.

| Suite | Command | Exit | Passed | Failed | Errors | Skipped | Seconds | Raw JUnit |
|---|---|---|---|---|---|---|---|---|
| fast | `/opt/homebrew/bin/uv run pytest -q -m 'not slow' --junitxml=/Users/santoshkumar/myprojects/Duress/docs/measurements/release-runs/20261026T143735Z-9431df52/01-fast/junit.xml` | 1 | 222 | 2 | 0 | 7 | 528.72 | `docs/measurements/release-runs/20261026T143735Z-9431df52/01-fast/junit.xml` |
### Run `20261026T144731Z-b6bd12c0`

Observed at: `2026-10-26T14:47:54+00:00`. Environment: Python `3.12.14`, SQLite `3.53.1`, `macOS-27.0-arm64-arm-64bit`.

| Suite | Command | Exit | Passed | Failed | Errors | Skipped | Seconds | Raw JUnit |
|---|---|---|---|---|---|---|---|---|
| pytest | `/opt/homebrew/bin/uv run pytest tests/integration/test_matrix_m2.py::test_s9_fixed_second_payment_is_an_order_duplicate tests/unit/test_exec_semantics.py::test_c10_rendezvous_releases_by_parties_then_timeout_then_not_at_all -q --junitxml=/Users/santoshkumar/myprojects/Duress/docs/measurements/release-runs/20261026T144731Z-b6bd12c0/01-pytest/junit.xml` | 0 | 2 | 0 | 0 | 0 | 22.78 | `docs/measurements/release-runs/20261026T144731Z-b6bd12c0/01-pytest/junit.xml` |
### Run `20261026T144814Z-2f711a0e`

Observed at: `2026-10-26T14:54:37+00:00`. Environment: Python `3.12.14`, SQLite `3.53.1`, `macOS-27.0-arm64-arm-64bit`.

| Suite | Command | Exit | Passed | Failed | Errors | Skipped | Seconds | Raw JUnit |
|---|---|---|---|---|---|---|---|---|
| fast | `/opt/homebrew/bin/uv run pytest -q -m 'not slow' --junitxml=/Users/santoshkumar/myprojects/Duress/docs/measurements/release-runs/20261026T144814Z-2f711a0e/01-fast/junit.xml` | 0 | 225 | 0 | 0 | 7 | 383.21 | `docs/measurements/release-runs/20261026T144814Z-2f711a0e/01-fast/junit.xml` |
