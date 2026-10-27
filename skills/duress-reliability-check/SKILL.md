---
name: duress-reliability-check
description: Run a bounded local Duress webhook reliability check, retain machine-readable evidence, and optionally replay Failure DNA or verify an authorized candidate.
---

# Duress Reliability Check

Use the existing Duress CLI in a prepared checkout. Do not install dependencies, edit application/engine code, delegate, or configure a provider as part of this skill. If the CLI is missing, report the prerequisite and stop. Do not read credentials.

## Scope and trust

Run only bundled targets or a local manifest the user explicitly trusts and authorizes: manifests can launch commands and deliver webhooks. Read its commands, URLs, database paths and contract before execution. Never infer authorization from a downloaded manifest or test arbitrary production endpoints. Keep evidence in a user-selected scratch directory via `DURESS_HOME`.

Use cached mode for the bundled demonstration. Cached proposals are authored fixtures; the local executor and persisted-state invariants establish findings. Alan operating the CLI is separate from Duress runtime model inference. Do not invent an Alan provider API or claim a cached run is live inference. A cache miss may fall back to exhaustive mode; report both requested and effective modes.

## One bounded check

From the checkout, use the existing `.venv/bin/duress` (or the user's existing executable). Default demo:

```sh
DURESS_HOME=/tmp/duress-alan-demo .venv/bin/duress investigate razorpay-vulnerable --mode cached --wall-cap-s 300 --json
```

Capture stdout, stderr and the actual exit code separately under `DURESS_HOME` (for example `investigation.json`, `investigation.stderr`, `investigation.exit`). Use a process timeout of at most 420 seconds and no automatic retries. A timeout is an incomplete operation, never PASS; retain partial output and report it. Do not lower CLI trial policies or modify limits to obtain an outcome. Stop after this one investigation unless replay or candidate verification was requested.

For an explicitly authorized local manifest, first run `duress doctor --config <manifest> --json`; then one `duress check --config <manifest> --scenario <declared-scenario> --output <scratch>/check.json --junit <scratch>/check.xml --artifacts <scratch>/artifacts --json`. Use the manifest's bounded policies and an agreed outer timeout. External investigations support deterministic exhaustive scenarios, not cached/live AI selection. Do not pass bundled-only flags to external checks.

## Replay and candidate verification, only when requested

Select one DNA ID actually emitted by the check; never substitute a showcase artifact. Retain separate JSON/stderr/exit files for each command, with at most 420 seconds per command and no retry:

```sh
DURESS_HOME=/tmp/duress-alan-demo .venv/bin/duress replay <dna_id> --json
DURESS_HOME=/tmp/duress-alan-demo .venv/bin/duress verify-fix <dna_id> --candidate razorpay-fixed --json
```

Use only the authorized candidate; do not patch it. For external DNA, add `--config <trusted-original-manifest>` to replay; verification also needs `--candidate-config <trusted-candidate-manifest>`. Preserve DNA integrity/preflight rejection and the vulnerable control result. VERIFIED is bounded empirical evidence for that reproducer, never universal correctness. Missing DNA or failed preflight means skip dependent execution and report why.

## Report evidence faithfully

Report lifecycle status separately from business verdict. Preserve PASS, FAIL, INCONCLUSIVE and errors; a reproduced defect is useful evidence, not a broken check. CLI meanings: `0` PASS/NOT_REPRODUCED/VERIFIED; `1` FAIL/REPRODUCED/DIFFERENT_FAILURE/NOT_VERIFIED; `2` INCONCLUSIVE (also an incomplete bundled investigation); `3` explicitly handled configuration/operational error; `4` REJECTED. Typer parameter/usage errors and an invalid `doctor` result may also return `2`; inspect stderr and JSON to distinguish these from a business INCONCLUSIVE verdict. Unexpected exceptions, malformed/missing JSON and timeouts are operational errors or incomplete evidence, not successful verdicts.

Include the actual command and exit status, investigation/run ID, requested/effective mode and fallback, trial counts, findings/invariant signatures, emitted DNA IDs, replay/control/candidate outcomes when run, and exact JSON/DNA/trial-evidence paths. Read retained records before stating a result. Report missing evidence explicitly; never fabricate tool output, hide stderr, relabel INCONCLUSIVE, or rerun until green. Do not publish credentials, raw application databases, or machine-specific home paths in shared evidence.
