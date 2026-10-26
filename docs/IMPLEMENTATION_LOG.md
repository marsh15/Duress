# Implementation log

Milestone progress, engineering problems that were diagnosed rather than
patched around, and the verified outcome of each checkpoint. Claims here are
tied to a command that was actually run; where an environment prevented a
check, the entry says so.

## M0 — reproducible defect (commit 60fb965)

Delivered the reference integration (`vulnerable`, `fixed`), the independent
`tests/m0` harness, the M0 gate and generated evidence. Verified locally and in
Docker: E1 20/20 double fulfils, E2 20/20 exactly-once at 2 and 5 parties, E3
10/10 clean drains after SIGTERM, target suite green, and the four gate tests
collected exactly.

## M1 — trusted execution, oracle and isolation (commit cfdf2b4)

Delivered limits, models, `exec@1`, the phased supervisor, the isolated provider
stub, the six-member invariant pack, expectations, the oracle, aggregation, S1,
S2, S3, S8, the fixture controls, and `duress run` / `sweep`.

### M1 verification interruption and recovery

The first M1 evidence run was interrupted. It left three problems:

1. `docs/measurements/e1/e2/e3.json` had been overwritten by a macOS run of the
   M0 experiments launched by the interrupted script.
2. `docs/measurements/m1.json` recorded 2 failures from **before** the working
   tree fixes: a `PermissionError` in the supervisor and a retry test that
   produced two timeouts.
3. The uncommitted "fix" for the first failure treated `EPERM` from `killpg` as
   proof that a process group had terminated.

Recovery:

- Restored `e1/e2/e3.json` to the last complete Docker evidence rather than
  keeping the interrupted macOS overwrite. Those experiments were not re-run
  before the M1 code changed, so their numbers still describe the same target
  bytes.
- Replaced the `EPERM` shortcut with process-table liveness (D-M1-10). A probe
  showed `killpg` can report `EPERM` for a group containing a live member, so
  the shortcut could have allowed state to be read while a target was writing.
  `tests/unit/test_group_liveness.py` covers the refusal, the zombie-only case,
  per-pid escalation, own-group protection, and a sweep that must keep an
  unkillable group's pidfile.
- Fixed the retry test by removing the sub-100 ms latency assumption it was
  never testing (D-M1-11), and documented the C2/C3 margin changes.

**Verified outcome (this tree):**

| Check | Command | Result |
|---|---|---|
| Fast suite, local | `uv run pytest -q -m "not slow"` | 147 passed, 3 deselected, 187 s |
| Fast suite, container | `docker run ... duress:m1 uv run pytest -q -m "not slow"` | 147 passed, 182 s |
| M0 gate, local | `scripts/m0_gate.sh --gates-only` | pass, 4 gate tests, 75 s |
| M0 gate, container | `docker run ... scripts/m0_gate.sh --gates-only` | pass, 4 gate tests, 81 s |
| Evidence | `scripts/m1_evidence.py --label local|docker` | exit 0, `docs/EVIDENCE.md` regenerated |

Measurements: `docs/measurements/m1.json` and `m1-docker.json` were written by
those runs. The failed 12:33 measurement was not kept as current evidence.

## M2 — library, preflight, replay, verify-fix, API

Delivered S5, S6 and S9 with the real registry entries; `EXHAUSTIVE_SPECS` and
`EXHAUSTIVE_SET` (seven); Failure DNA build and store; preflight; replay;
verify-fix; the API slice; `duress dna build|list`, `replay`, `verify-fix`,
`serve`; `render.yaml`; `scripts/smoke.sh` and `scripts/portability.sh`.

### Engineering problems and corrections

1. **S6's mechanism was not obvious from the schedule.** The first attempt's
   client timeout does not stop the handler: E4 measured the effect committed in
   10/10 trials. S6 therefore fails on the vulnerable target only because the
   retry arrives while the first handler is still awaiting the provider, so both
   pass the vulnerable check. The fixed handler serialises them under
   `BEGIN IMMEDIATE` and one wins. The test asserts that every observation has a
   timed-out first attempt and a second attempt, so a future change that removes
   the race cannot silently keep the FAIL.
2. **Fixture targets failed preflight.** The verify-fix truth table requires the
   fixture controls, but their profile id differed from the artifact's contract.
   D-M2-3 records why the control target now declares `razorpay-ref`.
3. **Two preflight tests initially asserted the wrong rejection.** Editing an
   embedded definition without recomputing the outer digest is correctly
   `tampered:digest`, because step 2 hashes the whole reproducer. The tests now
   recompute the outer digest to reach the inner-hash branches.
4. **`inf` in candidate sizing.** A first version of the unit test expected
   `candidate_size(0.95) is None`; the formula gives 20 because a 95% failure
   rate is easy to rule out. The test and the reasoning were corrected, not the
   implementation (D-M2-4).

### Verified outcome

| Check | Command | Result |
|---|---|---|
| Full fast suite | `uv run pytest -q -m "not slow"` | 198 passed, 3 deselected, 400 s |
| M2 acceptance | `scripts/m2_evidence.py --docker` | 51 passed, 161 s |
| Artifact | generated by `duress dna build` | `e8007dd56adae8ae`, INV-1, p0 1.0 (10/10), parties |
| Portability | `scripts/portability.sh` | exit 0: build in home A, artifact-only home B, REPRODUCED, VERIFIED |
| Container | `--docker` smoke | health ok on 0.0.0.0, replay REPRODUCED, verify-fix VERIFIED |

The DNA id is identical across independent builds and across the macOS host and
the Linux container, which is the intended canonical-identity property.

## M6 web product experience after M2 (upstream release snapshot: 4743a5e)

This entry records the frontend release scope and evidence from the upstream workbench snapshot; its counts are not combined with the separate M3/M4 backend acceptance below.


- Confirmed the clean checkout and remote `main` at `d08409c` before implementation. The pre-change fast gate passed: 198 tests, 3 deselected. The assignment was narrowed to frontend, ordinary API integration, documentation and release preparation; no unfinished engine, reducer or target changes are included.
- Added the Jinja/HTMX presentation layer, read-only investigation records, bundled M2 showcase, and saved replay/verification result metadata. The existing M0–M2 execution services and targets remain unchanged.
- Packaged only retained M2 DNA and measurement evidence. The showcase explicitly distinguishes aggregate/representative data from a full trial stream and from live execution.
- Browser verification detected low-contrast muted text and navigation; correcting those colors is part of the web checkpoint. Final gate results and commit references follow below after execution.
- Verified the product path with 7 passing Playwright tests and zero serious/critical axe issues on the three main pages. Docker startup, read-only smoke, actual portable replay/verification and 26 focused container tests passed.
- Independent Luna review found a misleading rejected-import message and malformed DNA metadata handling; fixed both. Final presentation inspection also separated the showcase badge from portable JSON; 27 focused tests passed after that change.
- The first broad post-change run had 222 passes and two failures in unchanged backend tests. Both passed unchanged in a focused diagnostic rerun. A serial broad rerun follows; the failed evidence is retained. No backend test assertions were relaxed.
- Checkpoint `4743a5e` — evidence workbench, ordinary API record integration, showcase, reports, screenshots, browser tests and release tooling committed and pushed to `origin/main`; `git ls-remote` confirmed `4743a5e3ab611c8e2e6568006144af6b55456fb0` on the remote. A fresh clone from GitHub was prepared for the final environment check.
- Final serial fast gate passed: 225 tests, 7 opt-in browser skips, no failures in 383.21 s. Both the failed broad run and the passing serial record are retained in the evidence directory. No original backend test or trusted execution file changed.
- Verified a fresh GitHub clone of `4743a5e`: frozen install and 27 focused tests passed on Python 3.14.7. Rebuilt final Docker image: 27 focused tests passed on Python 3.12.15; public/API smoke again returned REPRODUCED and VERIFIED; sweep reported no remaining recorded processes. Final evidence is in `docs/measurements/web-final.json`.
- At the M6 release snapshot, Render remained unverified. Live model execution, investigation creation and reduction were outside that frontend assignment; the separate backend M3/M4 acceptance below records later backend-branch work. No merged-branch acceptance result is stated here.

## M3/M4 interrupted-session recovery

Work resumed in `/Users/santoshkumar/myprojects/Duress-core` on
`feat/investigator-reduction`, with HEAD `d08409c`. The initial status/diff and
all 31 changed/untracked files were preserved in
`.recovery/20261026-202011/uncommitted-files.tar.gz`, alongside the binary patch,
HEAD, status and inventory. Archive SHA-256:
`209d5f10bbbfd387cb6610c476fd3204b211511f967a86d6a579d1687ef81d15`.
No interrupted work was discarded. The primary frontend checkout was only read
to inspect overlapping API routes; it was not modified or merged.

`uv sync --python 3.12` selected CPython 3.12.12, recreated this worktree's
virtualenv and installed the locked dependencies. `uv pip check` reported all
29 installed packages compatible. No hosted model credentials were present.
Luna subagents at `xhigh` independently checked the client, reducer and API/CLI;
the parent inspected their changes and integrated the corrections.

### Diagnosis and corrections

- Initial `uv run pytest -q tests/integration/test_investigation_e2e.py`:
  **2 passed, 2 failed**, 11.95 s. Saved outcome_detail and trial rows showed
  correctly matched parameterized scenario IDs but zero valid trials. The
  fixture's global AsyncClient monkeypatch blocked local readiness/delivery,
  producing `harness_error:AssertionError` throughout. The investigator's
  inconclusive classifications were honest; manufacturing a confirmation would
  have hidden the harness failure. The corrected guard blocks provider calls
  and non-loopback HTTP, and the fixed-target assertion now requires PASS.
- E2E rerun: **4 passed**, 101.89 s. Vulnerable H1/H2 each confirmed in 5/5
  exploration trials; naive_fix H1 refuted with 5 valid S3 passes and H2
  confirmed in 5/5 S5 trials; fixed PASS with both refuted. Vulnerable and
  naive_fix each produced one DNA from 30 total trials; fixed used 20 with no
  DNA. The final acceptance also exercises investigation-triggered reduction.
- A separate regression exposed refutation after only a subset of linked
  scenarios ran. Missing experiments now retain an inconclusive outcome.
  Failed characterization trials are counted, and a different characterization
  signature cannot be attached as the original finding's DNA. A separate
  attribution regression confirmed that a hypothesis confirmed by one scenario
  must not be attached to a different failure from another linked scenario;
  finding linkage now also matches the predicted check, kind and role.
  Investigator/orchestrator/mode rerun after that fix: **111 passed**, 3.07 s.
- HTTP-fixture review fixed total timeout, reported-token budget enforcement in
  live/cached modes, corrupt-cache search and secret redaction before error
  truncation. Standalone client check: **50 passed**. Parent mode tests verify
  timeout and token-overrun fallback mappings.
- Reducer review fixed non-finite/inconsistent preconditions, exact surviving
  step order/concurrency checks, incomplete trial evidence, false REDUCED on
  failed replay revalidation, and missing revalidation/black-box trial counts.
  Delegated checks: **172 unit tests passed**, **8 integration tests passed**
  (69.02 s). Parent added shared investigation deadlines and a zero-trial
  expired-deadline regression; the final unit reducer count is 173.
- API review fixed queued/running reduction records and terminal error
  persistence for unexpected investigation/differential service errors.
  Delegated API/CLI/naive_fix check: **38 passed**, 44.98 s. Parent fixed busy
  refusals consuming quota, validated differential parameters before admission,
  and returned CLI exit 2 for inconclusive differential cells.
- Real differential/S5 verification plus the CLI inconclusive regression:
  **3 passed**, 66.28 s. All nine explore cells agreed; S5 DNA on naive_fix was
  NOT_VERIFIED with `still_reproduces`.

The initial failures and newly exposed regression failures were diagnosed and
fixed; no assertions were relaxed, no engine verdict was overridden, and no
committed measurements were rewritten. See `DECISIONS.md`, `M3_M4.md` and
`BACKEND_HANDOFF.md` for the contracts, artifact semantics and frontend merge
resolution. The genuine hosted live inference/five-run comparison remains
unverified; authored cache provenance and controlled HTTP responses do not
substitute for that experiment.


### Independent final review and C.11/lifecycle closure

The first consolidated M3/M4 acceptance command passed **390 tests**, with one
Starlette/httpx deprecation warning, in 295.04 s. A broad run collected before
the final review fixes completed with **580 passed, 3 deselected**, 657.18 s;
it is retained locally as earlier evidence, not the final-code sign-off.

The independent Luna review identified three real gaps: group concurrency
counted unrelated deliveries, the required fault-free experiment was opt-in,
and cancelled/stale API records could remain queued/running. New regressions
failed at each seam before correction. Static admissibility now counts each
affected role; public reduction includes fault-free characterization and stores
black_box_rate; investigation admission reserves all 62 possible reduction
stage trials. API lifespan composition cancels/drains tasks on shutdown and
marks stale records failed on startup. The re-review found all three blockers
resolved with no remaining finding in that scope. No process-liveness or
preflight-digest rules changed.

The final reducer unit suite contains 175 cases; lifecycle regressions cover
all three asynchronous operation types plus restart reconciliation. Final
consolidated acceptance, full regression and matrix results follow below.


### Final consolidated acceptance

The frozen-code command below completed with **402 passed, 0 failed**, one
Starlette/httpx deprecation warning, in **314.44 s**:

```sh
env DURESS_RECORD_MEASUREMENTS=0 uv run pytest -q \
  tests/unit/test_llm.py tests/unit/test_investigator.py \
  tests/unit/test_orchestrator.py tests/unit/test_reduce.py \
  tests/unit/test_cli_outcomes.py \
  tests/integration/test_investigation_e2e.py \
  tests/integration/test_investigation_modes.py \
  tests/e2e/test_exhaustive_fallback.py tests/integration/test_reduce.py \
  tests/integration/test_differential.py tests/integration/test_api.py \
  tests/integration/test_api_backend.py tests/integration/test_api_admission.py \
  tests/integration/test_api_lifecycle.py tests/integration/test_cli_backend.py \
  tests/integration/test_naive_fix_matrix.py \
  targets/razorpay_ref/tests/test_naive_fix.py \
  --basetemp=.recovery/acceptance-release
```

This includes **50 client**, **96 investigator**, **3 orchestrator**, **175
reducer unit**, **8 reducer integration**, **4 investigation E2E**, and **4 API
lifecycle** cases, plus modes, real exhaustive fallback, differential/S5
verification, target, admission and CLI contracts. The investigation with
reduction used 40 trials: 20 exploration, 10 failure characterization and 10
fault-free characterization. Its rendezvous candidates were all statically
pinned; the original DNA stayed primary with the claim "no admissible
reductions". Its observed black_box_rate was 10/10 in this test run; this is
recorded empirical evidence for this synthetic schedule and host, not a claim
about other schedules or environments. The latency reduction fixture separately
produced a reduced artifact, revalidated it and independently replayed it.

The separate longer CLI command also passed with exit code 0:

```sh
env DURESS_HOME="$PWD/.recovery/matrix-20-home" \
  DURESS_RECORD_MEASUREMENTS=0 \
  uv run duress differential --policy matrix --json
```

Its persisted record contains **9/9 agreeing cells**, **180 valid trials** (20
per cell), no disagreements or inconclusive trials, and no remaining pidfiles.
Observed cells: S3 vulnerable FAIL / naive_fix PASS / fixed PASS; S5 vulnerable
FAIL / naive_fix FAIL / fixed PASS; S9 PASS on all three. The full emitted JSON
and persisted matrix record are retained under `.recovery/` locally. This is
synthetic reference-target evidence, not hosted-model evidence.


### Final full regression and integrity checks

```sh
env DURESS_RECORD_MEASUREMENTS=0 uv run pytest -q -m "not slow" \
  --basetemp=.recovery/fast-release
```

Result: **592 passed, 3 deselected, 0 failed**, one Starlette/httpx deprecation
warning, **714.99 s**. The three deselected tests are the pre-existing measured
M0 experiments; the longer M4 matrix was run separately as above. The full
suite includes the M0–M2 oracle, isolation, preflight, replay and verify-fix
regressions. The final command ran on the frozen code alongside the independently
isolated matrix; no code edits followed.

`uv pip check` passed with all 29 installed packages compatible. Route
method/path inventory found no duplicates. Diff and secret-pattern reviews
passed. SHA-256 comparison verified all **12 committed measurement JSON files
unchanged**, and the 31-file recovery archive still matches its recorded digest.
Genuine hosted live inference and the five-run hosted comparison remain
unverified; no local model weights or unrelated cleanup were used.

### Documentation merge resolution (2026-10-26)

The M6 workbench and saved-operation evidence remains scoped to the upstream frontend release (`4743a5e`, evidence tail `b30edc4`); M3/M4 decisions and acceptance remain scoped to the backend branch (`f68ceb7`). Keep the upstream web pages/templates and `GET /api/operations/{operation_id}` record lookup. `api_backend.install_backend_routes()` owns investigation list/start/detail, hypothesis/findings, reduction and differential APIs, including their investigation GET routes. The overlapping upstream investigation GET handlers must not compete with those backend routes; web pages use the backend reads, while saved-result pages retain the upstream `GET /api/operations/{operation_id}` lookup. This note records ownership only; it makes no merged-branch test or route-inventory claim. No measurement files were rewritten for this documentation resolution.


### Combined merge verification (2026-10-27)

The user authorized resolving PR #1 against upstream main `b30edc4`. All work
remained in `Duress-core` on `feat/investigator-reduction`. The verified backend
head `f68ceb7` remains recoverable in Git and in the local
`refs/duress-recovery/pre-main-f68ceb7` ref; the original 31-file archive is
unchanged. No primary-checkout edits or history rewrites were used.

The five conflicts retained both ignore rules and documentation histories.
`api_backend` owns the one investigation GET/POST route set; shared record
readers include live and historical showcase records. Upstream web/report routes
and saved replay/verify operation records remain available. Replay/verify
responses retain outcome/preflight fields and the additive operation_id. The
web adapter handles M3 nested hypotheses/citations/statuses and scenario trial
rows alongside legacy records. No new UI start/reduction controls were added.

Verification on the combined code:

- Focused API/CLI/web/records/measurement-tool checks: **65 passed**, one
  Starlette/httpx deprecation warning, **2.32 s**. This includes new regressions
  for route uniqueness, a shared operation gate, saved response equality, and
  invalid-local-record fallback behavior.
- `env DURESS_RECORD_MEASUREMENTS=0 DURESS_BROWSER_TESTS=0 uv run pytest -q
  -m "not slow" --basetemp=.recovery/merge-full`: **624 passed, 7 skipped,
  3 deselected, 0 failed**, one deprecation warning, **742.76 s**. Browser checks
  were skipped in this command because they were run separately below; the
  three slow tests are the existing M0 measurement experiments.
- `env DURESS_RECORD_MEASUREMENTS=0 DURESS_BROWSER_TESTS=1 uv run pytest -q
  tests/browser --basetemp=.recovery/merge-browser`: **7 passed**, **46.65 s**.
  These use the repository's local Playwright workflow and existing browser
  installation, including actual replay/fix verification and accessibility.
- `uv sync --python 3.12` and `uv pip check`: Python **3.12.12**, all **34**
  installed packages compatible with the upstream Jinja/Playwright dependencies.
- Independent Luna xhigh review found no functional integration blockers. The
  index was subsequently confirmed to have no unresolved entries.

Diff/marker checks pass. All **28** upstream/backend measurement files retain
their pre-verification hashes; ordinary pytest did not rewrite historical
evidence. The earlier 402/592 backend-only results remain scoped to `f68ceb7`;
the counts above describe the merged tree. Hosted live inference remains
unverified, as before.
