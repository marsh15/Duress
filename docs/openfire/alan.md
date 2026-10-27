# Alan integration and provenance

Duress includes a reusable [Duress Reliability Check skill](../../skills/duress-reliability-check/SKILL.md) for Alan users. It operates the existing local CLI: one bounded check, machine-readable evidence, and optional Failure DNA replay/candidate verification. It preserves PASS, FAIL, INCONCLUSIVE and operational errors. It requires a prepared checkout; it does not install software or implement an inference adapter.

**Fresh invocation status, 2026-10-27: unverified.** The skill was authored and its frontmatter checked, but this session could not complete import or submit a model request through Alan Desktop's UI automation. No fresh Alan session ID, CLI execution, exit code, investigation, replay or candidate-verification result is claimed. This is an application/UI-automation blocker, not a lack of user authorization for normal signed-in Alan credits.

## Use in Alan

Alan documents skills as folders containing `SKILL.md`, optional supporting files, and a composer `/` menu. In Desktop, open **Skills → Import → From a folder**, select this checkout's `skills/duress-reliability-check` folder, review it and add it to the team. Where already discovered, **From this computer** can import the skill instead. Imported copies are managed by Alan; publish later changes through its editor. These are documented instructions, not a claim that this import succeeded here. [Official Skills guide](https://docs.tryalan.ai/docs/skills/skills).

Start a session on **This computer**, choose the prepared Duress checkout and **Alan Code · DeepSeek V4.1 Flash**, then type `/`, filter `duress-reliability-check`, and select its skill chip. The model and execution location must be explicit. [Start a session](https://docs.tryalan.ai/docs/agents-and-sessions/start-a-session), [coding agents and models](https://docs.tryalan.ai/docs/agents-and-sessions/coding-agents-and-models), [local execution](https://docs.tryalan.ai/docs/cloud-and-local/local-execution).

Send this bounded request (replace `<checkout>` with your local path):

```text
Use /duress-reliability-check in <checkout>. Use the existing .venv/bin/duress,
with DURESS_HOME=/tmp/duress-alan-demo. Run exactly one investigation of the
bundled razorpay-vulnerable target in cached mode with --wall-cap-s 300 --json.
Capture stdout, stderr and actual exit status as investigation.json,
investigation.stderr and investigation.exit under that scratch directory.
Use an outer timeout of at most 420 seconds; do not retry.
If that completed run emits DNA, replay exactly one emitted DNA ID once and
verify it once against the bundled razorpay-fixed candidate, retaining separate
replay.json/replay.stderr/replay.exit and verification.json/verification.stderr/
verification.exit files. Use at most 420 seconds per command and no retries.
Report actual lifecycle, verdict, requested/effective mode, fallback, trial
counts, findings, DNA IDs, control/candidate outcomes and retained evidence paths.
Do not install, edit code, commit, push, delegate, read credentials, or use live
Duress inference. Cached proposals are authored fixtures. Stop after this scope.
```

The underlying demo command is:

```sh
DURESS_HOME=/tmp/duress-alan-demo .venv/bin/duress investigate razorpay-vulnerable --mode cached --wall-cap-s 300 --json
```

Capture the process return code separately: exit `1` may mean a successfully reproduced defect. Never use `&&` to decide whether to retain its output. Replay and verification use IDs actually emitted by this investigation, not historical showcase IDs. See the skill for trusted local-manifest checks, outcome mappings and evidence requirements.

## Attempt made here

The signed-in Alan Desktop app was controlled only through CUA. Its composer displayed Alan Code · DeepSeek V4.1 Flash and the local Mac execution target. No login, subscription or credit purchase was needed or attempted.

The automation returned inconsistent state: accessibility content selected Skills while screenshots still showed the composer or earlier Settings page. Recoveries included rebinding/resetting CUA, selecting the tab directly and using the normal Window menu. Import exposed **From this computer** and **From GitHub**, but did not expose the documented **From a folder** option. Selecting **From this computer** returned to Skills without a completed import. The normal local-directory picker navigated to the intended checkout, but **Open** remained disabled even with that directory selected. The documented manual editor fallback opened, but attempting to fill it returned `elementHasNoFrame`; no skill was saved through that editor.

Other raw CUA errors included `Computer Use server error -10005: noWindowsAvailable` and `The element ID is no longer valid. Try to get the on-screen content again and see if that resolves the issue.` We stopped before sending an unverified or wrongly scoped request. No screenshot is published because the stale screenshot/accessibility mismatch would be misleading proof of the operation.

The skill's YAML parsed successfully with Ruby's standard YAML parser, with exactly the required `name` and `description`. The skill-creator `quick_validate.py` could not run because existing Python environments lack `yaml`/PyYAML; no package was installed. CLI examples and outcome mappings were checked against this checkout's CLI and README. Actual Alan behavioral validation remains outstanding.

## Genuine historical Alan evidence

[Sanitized machine-readable evidence](alan-evidence.json) preserves four previously observed Alan-engine sessions from 2026-10-26. Three used provider `alan`, model `deepseek/deepseek-v4.1-flash`, totaling 349 assistant messages and 422 completed tool calls. The fourth review session used `meta/muse-spark-1.3`; no claim about that model's license is made.

The original M0 session `ses_e887840b9ffeZwWM3TgZF4unDE` records implementation, CLI/test activity and commit `60fb965`. Its historical completed bash tool reference `prt_1778f47f6001ocvR1oxXmNRRw9` records exit metadata `0`. These are prior discovery observations, not fresh checks performed by this skill attempt. Matching foundational commits include:

- `60fb965e52762c4d52df7a1c6ad15dc2e67ccda2`: M0 reference integration and concurrency reproduction.
- `cfdf2b48774443a92cd38ecfb82692aec601b3bd`: M1 execution engine, oracle and isolation.
- `f5019c91f6354f175fb7021e799ccc5c4d98a248`: M1 follow-up.
- `d08409c510be35efce05dd52b94c202b95134508`: M2 Failure DNA and replay.

Evidence retains session/message/part identifiers, timestamps, model identities, tool counts and final-text hashes. Home paths are replaced with placeholders. Raw Alan databases/WAL files, credentials, account identifiers and raw tool outputs are not published.

A bounded provenance statement is: “Duress's reference integration, execution engine and Failure DNA foundation were implemented with Alan Code using Alan-served DeepSeek V4.1 Flash; original Alan sessions and matching commits are preserved.” Later release, productization, UI, validation and this skill/documentation work were performed through other agents. Do not describe the entire current release as exclusively Alan/open-model work or claim organizer acceptance.

Alan driving the CLI is distinct from Duress's internal model client. Bundled cached responses are **authored fixtures**, not Alan runtime inference. Genuine hosted Duress live inference remains unverified; this integration supplies no invented Alan provider URL/API and does not reuse Desktop credentials. [DeepSeek's model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) documents the model weights' license; it does not attest Alan's exact served checkpoint.
