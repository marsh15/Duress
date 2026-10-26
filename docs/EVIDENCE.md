# Duress — Evidence

The latest integrated release observations are in the final section below. Earlier milestone sections retain their original scope, including historical pending-feature statements.

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

## Final integrated product acceptance — 2026-10-27

The final pass enabled investigation creation/progress, source-grounded hypotheses and findings, actual schedules and retained business-state views, reduction, and differential comparison in the existing Jinja/HTMX application. Execution remains in the shared services. The runner, supervisor, oracle, invariant pack, preflight, DNA, replay, verifier, reducer, semantics, and target implementations are unchanged from `1a106c0`. Investigation persistence adds the exact built schedule/control level; record reads now reject malformed roots and symlinks consistently.

Product implementation is `00532f4`; the PID-1 signal fix is `93eac20`; image recovery-archive exclusion is `1ad9711`; product documentation and actual demo records are `91af35e`; populated-record accessibility fixes are `4cdb718`.

### Python regression and browser evidence

- `DURESS_RECORD_MEASUREMENTS=1 DURESS_BROWSER_TESTS=0 uv run --python 3.12 python scripts/measure.py --run fast`: **666 passed, 10 browser tests skipped, 3 slow measurements deselected, no failures**, 666.86 s. Python 3.12.12, SQLite 3.53.4. The uniquely named [run record](measurements/release-20261026T202054Z-1d7e6e4f.json) retains the actual command, source digest, environment, and [JUnit](measurements/release-runs/20261026T202054Z-1d7e6e4f/01-fast/junit.xml). The three slow measurements and browser cases were executed separately below. The Docker-ignore regression was added after broad collection and passed in the separate four-case runtime check.
- `DURESS_RECORD_MEASUREMENTS=0 DURESS_BROWSER_TESTS=1 uv run --python 3.12 pytest -q tests/browser --basetemp=.recovery/final-browser-accepted --junitxml=.recovery/final-browser-accepted.xml`: **10 passed in 159.76 s**. [Raw JUnit](release/2026-10-27/browser-accepted.xml). This includes the full real investigation → DNA → retained state → replay → verification → reduction → differential journey, plus navigation, import rejection, token memory, keyboard, responsive and axe checks.
- The subsequent populated-record audit found low-contrast numerical markers, status-band class interference, non-focusable scrolling evidence, and mobile overflow from long scenario IDs. The corrected pages have **zero serious/critical axe violations** and fit **390/768 px** viewports in the [actual-record checks](release/2026-10-27/real-record-accessibility.json). Table and JSON scrolling are keyboard-focusable. The expanded browser journey includes these populated checks. Its suite returned **9 passed, 1 failed in 162.23 s**: only a test locator was ambiguous because both the visible explanation and newly inspectable raw record contained the same text. Scoping that assertion to the visible explanation passed the targeted navigation rerun: **1 passed in 4.02 s**, [JUnit](release/2026-10-27/browser-navigation.xml). The other nine cases, including the expanded full journey, had passed. No business verdict assertion was weakened. The failed suite is retained byte-for-byte as [compressed JUnit](release/2026-10-27/browser-populated-initial.xml.gz).
- A prior browser harness failure read a differential admission response body after immediate navigation discarded it. The test now reads the persisted ID from the actual navigation and checks its API record. That initial **7 passed, 1 failed** result is retained as [compressed JUnit](release/2026-10-27/browser-initial.xml.gz); its targeted complete-journey rerun passed in 116.92 s ([JUnit](release/2026-10-27/browser-journey.xml)).
- Final affected presentation tests: `uv run --python 3.12 pytest -q tests/unit/test_web.py tests/unit/test_web_release.py tests/unit/test_investigation_workflow.py tests/unit/test_analysis_ui.py`: **34 passed**. Execution code did not change after the broad gate; later accessibility changes were checked with the browser and this focused suite.
- Fresh clone `/tmp/duress-release-clean-20261027`, `uv sync --locked --python 3.12`, then the profiles/records/measurement/web/investigation/analysis/runtime/release-record/API-merge suite: **78 passed in 4.71 s**, [JUnit](release/2026-10-27/clean-focused.xml). The clone installed 34 locked packages under CPython 3.12.12 and remained Git-clean.

### Separate slow measurements

The three existing slow experiments ran with `DURESS_RECORD_MEASUREMENTS=1` under Python 3.12. The invocation set `tests.m0.harness.MEASUREMENTS_DIR` to the new `docs/release/2026-10-27/slow-measurements` directory before `pytest.main(['-q', '-m', 'slow', ...])`. This changes only the output destination, not experiment code, assertions, or trials. Result: **3 passed, 677 deselected, 83.79 s**; [JUnit](release/2026-10-27/slow-measurements/junit.xml).

- [E4](release/2026-10-27/slow-measurements/e4.json): 10 valid fixed-target trials, 10 client timeouts, 10 committed effects.
- [E5](release/2026-10-27/slow-measurements/e5.json): 20 valid trials per condition; duplicate fulfilment observed in 20/20 fault-free, 20/20 50-ms latency, and 20/20 200-ms latency trials. These are new measured local rates, not general probabilities or a fault-free guarantee.
- [E6](release/2026-10-27/slow-measurements/e6.json): 10 valid trials; observed total p95 0.9348 s. Phase timings and individual rows are retained.

The 28 pre-existing historical measurement files retain their original SHA-256 hashes. New observations are separate files; no committed measurement or acceptance assertion was edited to obtain a pass.

### Executed product demonstration

[Retained records](release/2026-10-27/) are byte-for-byte copies from the passing local journey. Cached proposals are authored fixtures. Both H1 and H2 were confirmed by actual exploration, with 30 total investigation trials and a generated three-delivery DNA. Replay reproduced the signature in 10/10 valid trials; fix verification re-established 5/5 control failures and observed no failures in 20 valid corrected trials. Reduction returned NOT_REDUCED because rendezvous-preserving removal was inadmissible; its fault-free characterization separately observed 10/10 failures. The Explore differential returned 9/9 agreeing cells across 45 valid trials, including naive_fix S3 PASS / S5 FAIL and fixed S5 PASS.

Updated screenshots and the silent WebM walkthrough show actual saved records, not synthetic success output. The separate showcase capture stays labelled historical. [Demo instructions](DEMO.md) name the source records and reproduce the read-only capture command.

### Clean-clone CLI, accepted reduction, and final container

The clean clone was fast-forwarded to final application commit `38076bb`. A fresh runtime home initially held **only** exported DNA `0edb63c480dbc29b`. The real CLI commands and observed exits are retained in [the CLI manifest](release/2026-10-27/clean-cli/manifest.json), alongside complete output JSON:

- Normal fixed-target S1: PASS, exit 0.
- Exported-artifact replay: REPRODUCED, 10/10 valid matching failures, exit 1 deliberately.
- Corrected-target verification: VERIFIED, exit 0, with actual control/candidate counts and the conditional statement retained.
- Cached investigation: completed FAIL, exit 1; rendezvous reduction: NOT_REDUCED, exit 0.
- `duress differential --policy matrix --json`: **9/9 agreeing cells, 180 valid trials**, no disagreements/inconclusive cells, exit 0, 190.40 s.

A separate latency DNA (`2d5a9f256d7ab01c`) was built through the real CLI after a host-storage failure was resolved. The browser started its reduction through the normal form/API. [The actual reduction](release/2026-10-27/accepted-reduction/record.json) returned **REDUCED**: 3 → 2 deliveries, 3 accepted candidate trials plus 10 replay revalidation trials and 10 required fault-free characterization trials, **23 total**. Original and reduced schedules retained their latency fault, business expectations, invariant pack and roles. The accepted artifact is `8c6f00ad4cbc8a0f`. Its bounded claim is single-delivery minimality under the recorded predicate, not global minimality. The browser opened and downloaded the accepted DNA; its bytes matched the [independent replay input](release/2026-10-27/accepted-reduction/reduced-dna.json). In another empty home, that artifact [reproduced in 10/10 valid trials](release/2026-10-27/accepted-reduction/portable-replay.json). The accepted-result and derived-DNA pages also passed axe and 390/768-px checks ([checks](release/2026-10-27/accepted-reduction/browser-checks.json)). The original rendezvous NOT_REDUCED observation is preserved independently.

The final view correction at `38076bb` makes a null per-delivery retry override inherit the recorded delivery policy, displaying `1 attempt` instead of `None attempts`. Its dedicated regression and affected suites passed **31 tests**. The final clean-clone focused suite passed **79 tests in 3.14 s** ([JUnit](release/2026-10-27/clean-final-focused.xml)). The same 79 cases passed in Linux/Python 3.12.15 in **2.69 s**, [JUnit](release/2026-10-27/container/focused.xml) and [output](release/2026-10-27/container/focused.log). This later presentation-only change did not alter execution semantics or verdicts.

Final clean-checkout build: `docker build -t duress:release .`, exit 0. Image identity: `sha256:53f47629466fb4b800b059d837e6a6b4e731a7133fde725b2960846703a1cdf5`, Linux/arm64, Python 3.12.15. [Build log](release/2026-10-27/container/build.log). Non-secret probe files were deliberately placed in the clone's `.recovery/` and `.duress/`; `docker run --rm --entrypoint sh duress:release -c 'test ! -e /app/.recovery && test ! -e /app/.duress && test ! -e /app/.git'` confirmed exclusion.

[Final container observations](release/2026-10-27/container/summary.json) and [smoke output](release/2026-10-27/container/smoke.txt) retain these actual runs:

1. Normal container entrypoint with `PORT=8080`, `DURESS_HOSTED=true`, and a non-secret test token: public pages, target registry, DNA viewer, and all local scripts/styles loaded; missing-token replay returned 401. `scripts/smoke.sh <local-container-url> --dna showcase/dna/e8007dd56adae8ae.json` returned REPRODUCED and VERIFIED. The API process was PID 1, bound to `0.0.0.0:8080`.
2. A fresh container respected the normal three-operation quota while executing cached investigation → NOT_REDUCED reduction → Explore differential. The investigation used 30 trials and the differential had 9/9 agreements with zero inconclusive cells. Persisted records and result pages were read back. No quotas were bypassed.
3. A real investigation was interrupted by `docker stop --time 20`; after restart, its saved status remained failed with `cancelled`, the operation gate was clear, and process-table/pidfile checks found no managed target remaining. Every tested container stopped with exit 0.

The first final-image export was interrupted by a host disk-full condition (`StorageFull: No space left on device`), followed by Docker RPC EOF. The optional initial latency build consequently produced no usable JSON. Only disposable artifacts and the earlier image created by this release pass were removed; existing milestone images and unrelated data were preserved. After OrbStack restarted, the clean build and all checks above passed. Host free space remains low; this is an environment constraint, not a successful hosted deployment. No successful result was fabricated or substituted for the interrupted attempts.

### External verification and final audit scope

No `DURESS_LLM_*` runtime settings were available. Genuine hosted inference, provider interoperability, and the hosted multi-run live-versus-exhaustive comparison remain unverified. Cached authored-fixture and deterministic exhaustive execution are implemented and covered separately. No local model weights or Alan Code credentials were used.

Render-specific connected tooling, CLI credentials, and a logged-in dashboard session were unavailable; the browser reached Render's login page. No public deployment URL or hosted capacity claim is made. Deployment needs an authorized Render account/session or runtime API credential. Local Docker success does not replace that hosted check.

The final source audit preserved the critical execution files and target bytes, checked unique API route ownership, token/admission boundaries, safe record paths, autoescaping/JSON encoding, and actual process cleanup. Confirmed defects fixed in this pass were disabled web workflows, corrupt-record crashes/symlink reads, missing retained schedule/state presentation, container signal forwarding, recovery archives entering the image context, populated-record accessibility/mobile issues, and null retry presentation. No known correctness-blocking defect remains in the exercised local product paths. Relevant full/slow/browser/clean-environment results and development failures are retained above. All 28 historical measurement files still match their baseline hashes.
