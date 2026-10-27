# Duress evidence instrument

## Audit and reference lock

Baseline: productization commit `479a1f3`, with FastAPI, Jinja, HTMX and vanilla JavaScript.
The existing horizontal navigation, oversized introductory headings, tiny evidence text,
and hard-coded light surfaces obscure a capable investigation workflow. Findings,
authored proposals, historical aggregates and actual trials already have distinct data
contracts; retain those distinctions. Never reconstruct missing measurements.

Direct-build authority: the supplied Duress redesign brief. Preserve its charcoal canvas,
Geist typography, mint action emphasis, restrained boundaries, 248px sidebar, compact
context bar, and readable evidence density. Use an original parallel-lane checkpoint
mark. No bitmap illustration is needed for this code-native instrument.

Research: [Geist](https://vercel.com/geist/introduction) supplies component consistency
and semantic color roles; [Trigger.dev runs](https://trigger.dev/docs/runs) reinforces
the separation of run lifecycle and attempts. Refero's bundled craft references guide
native controls, focus, touch sizes and visual QA. Public Linear command-menu and
Langfuse trace-view URLs were unavailable during initial research; no claimed inspection.

| Decision | Source and role | Reason |
| --- | --- | --- |
| Charcoal / mint, designed light theme | User palette; mint for actions | Quiet canvas, clear interaction |
| Sidebar with workspace / evidence groups | User shell specification | Stable navigation across deep records |
| Separate lifecycle and verdict | User evidence-integrity rules | A completed run can reveal a failure |
| Local setup guide with CLI commands | Existing external registration boundary | Guide supported setup without changing trust |
| Ordered schedule lanes and measured attempts | Retained schedule / attempt contracts | Distinguish configuration from observation |
| Native dialog command palette | User keyboard specification, craft focus rules | Searchable navigation without dependencies |

## Page inventory

- Overview: first-run guidance, current records, recorded benchmark, target registry, DNA.
- Projects and project detail: registered manifests, adapter contracts, doctor, scenarios,
  investigations, replay/reduction/verification and persisted operations.
- Targets: reference profiles and controls.
- Investigations: creation, history, active progress, findings, scenarios, hypotheses,
  trial evidence, source references and retained business state.
- Failure DNA library/detail: import/export, configured schedule, attempts, violations,
  replay, reduction, correction verification.
- Reduction, differential comparison and operation detail: authoritative saved outcomes.
- Evidence: supervisor snapshot; report: portable static investigation presentation.

## Delivery and verification

Work in scoped checkpoints: foundations and shell; workflow/evidence refinements;
browser/accessibility/regression verification and screenshots. Preserve all API and
artifact encodings. Maintain real test results and outstanding limits below as work lands.

## Implemented system

- Geist Sans and Geist Mono variable WOFF2 are self-hosted from official `geist@1.8.2`,
  with the SIL OFL notice. Dark/light/system preference is the only stored browser state.
- `tokens.css` owns color, type, spacing, radius and overlay values. `site.css` retains
  evidence layouts with semantic values; `workspace.css` owns the app shell and shared
  components. No frontend framework, chart library or animation dependency was added.
- The primary dark palette follows the brief: canvas `#0C1115`, sidebar `#10171B`,
  surface `#151C20`, elevated `#1C252A`, primary text `#EDF4F3`, action `#6FDFC9`.
  Success `#75D7A7`, failure `#FF8089`, caution `#F3C878`, information `#86BFFF`.
- Light surfaces use `#F5F7F6`, `#EFF3F1`, `#FFFFFF`, with text `#162328` and action
  `#086B5D`. Muted light text was darkened to `#586A70` after axe found contrast defects.
- Typography: 30px page titles, 19px section headings, 14px body, 12px supporting text;
  identifiers and numerical evidence use Geist Mono. Shared radii are 7/10/14px.
- Sidebar groups: Workspace (overview, projects, investigations, targets), Evidence
  (Failure DNA, comparisons), and guide/configuration. The context bar stays compact.
  Local registered projects populate a real project switcher and palette commands.
- Cmd/Ctrl+K opens a native dialog; arrows, Enter, Escape, filtering and focus restoration
  work without a dependency. The mobile drawer traps focus and makes the background inert.
- Overview distinguishes live records, retained findings, historical benchmarks, reference
  targets and DNA. Readiness comes from the actual health and project registry APIs;
  registration never implies observer connectivity or successful validation.
- `/guide` explains the five local setup checkpoints; `/design-system` exercises shared
  components. Local manifests and arbitrary command/destination registration stay in the CLI.
- Investigation history supports search, lifecycle filtering and target-name sorting.
  Detail leads with execution status and business verdict, followed by actual findings,
  recorded phases, provenance, scenarios, state, hypotheses and trial evidence.
- DNA library search, copy feedback, downloads, replay, correction verification, reducer
  views, operation records and differential matrices share the same component vocabulary.
  AGREES is neutral; a confirmed observed defect is a failure indication.
- Configured delivery groups are explicitly ordered-step diagrams. Measured bars are
  derived only from retained `started_s`/`ended_s` and share a relative time axis.
  Native inspectors show the exact selected retained record. Missing timing stays unavailable.
- External before/after comparisons account for duplicate row contents. Changed values
  appear as removal/addition, without assuming a primary key. Incomplete, invalid,
  unsettled or missing collections never become inferred absence. Existing snapshot
  and invariant tables remain available for the full evidence.
- External saved operations poll their real persisted state and stop on completion,
  failure, interruption, timeout or navigation. Refresh failures remain visible in context.
- Standalone reports embed the theme CSS and fonts, present structured scenario outcomes,
  retain complete evidence, and omit browser-only actions. Report generation executes nothing.

## Authentic visual evidence

All images are real local Chromium captures, not mockups or manufactured result data.
The completed example `inv-20261027T044209-843af6` was started through the redesigned UI,
used an authored cached proposal, executed its trials, completed with business verdict
FAIL, and produced DNA including `0edb63c480dbc29b`.

- [Dark overview](screenshots/overview-dark.png)
- [Light overview](screenshots/overview-light.png)
- [Completed investigation](screenshots/investigation-completed.png)
- [Failure DNA](screenshots/failure-dna.png)
- [Configured delivery sequence](screenshots/delivery-sequence.png)
- [Mobile setup](screenshots/mobile-setup.png)
- [Silent walkthrough](screenshots/walkthrough.mp4)

The walkthrough navigates actual completed evidence and setup views. It is not a recording
of a fresh replay, and does not imply a live model ran. Historical release measurements
and screenshots were preserved in their original locations.

## Scope and remaining limits

- Hosted mode continues to prohibit external projects. Local registration remains a CLI
  action; the web guide is not an arbitrary manifest editor or a validation simulator.
- External project selection navigates to its project-specific actions. Reference cached,
  exhaustive and live investigation modes retain their existing supported scope.
- No unretained source files, timing, row identity, future phases, confidence percentages
  or missing observations are reconstructed. Source views show retained quotations and
  validator status, not a claim of freshly reading arbitrary source files.
- Timelines fit retained attempts; no zoom/playback controls are added where the current
  data does not warrant them. Reports retain timing tables rather than executable inspectors.
- Automated accessibility covers serious/critical axe findings and keyboard flows; it is
  not a certification or a full screen-reader audit. No physical-device, Firefox/WebKit,
  hosted live-model or container responsiveness measurements were performed in this work.
- PR #2 remains open. The redesign starts at its checked `479a1f3` head and must reconcile
  any subsequent productization changes before merging into main.

## Verification results (2026-10-27)

- Python 3.12.12: **577 passed**, running `uv run --python 3.12 pytest -q tests/unit tests/integration/test_web_api.py tests/integration/test_dna_journey.py`.
- Python 3.14.7: the equivalent earlier regression run passed **576**, before the additional hosted-shell case.
- Chromium: `DURESS_BROWSER_TESTS=1 uv run pytest -q tests/browser` → **27 passed in 204.18s**. The suite runs actual APIs and target processes; replay/download bytes, persisted status, verification truth, reduction and matrix outcomes are asserted.
- The suite includes both-theme axe scans across seven routes, keyboard command/menu and drawer focus tests, filters, timing inspection, and viewport checks at 320, 390, 430, 768, 1024, 1440 and 1600px across applicable cases. There were no serious/critical axe violations in the checked states.
- `node --check` passed for the changed JavaScript. `git diff --check` passed.
- `uv build --wheel` passed. Wheel contents include fonts, the OFL notice, themes and new templates. Standalone-report tests verify embedded fonts/license and no script or button dependency.
- A fresh worktree needs `.duress/tmp` created before the direct replay integration tests (`mkdir -p .duress/tmp`). Initial missing-directory failures were resolved by supplying that test prerequisite.
- The test environment emits an existing Starlette/httpx TestClient deprecation warning; this work does not change that dependency contract.

## Measured frontend budgets

Static raw-size budgets: JavaScript including vendored HTMX ≤128 KiB; CSS ≤100 KiB; both fonts ≤160 KiB. All pass:

- JavaScript including HTMX: **98,997 bytes**.
- CSS: **78,748 bytes**.
- Fonts: **140,664 bytes**.

Separate-file gzip estimates: JavaScript including HTMX 30,537 bytes; CSS 15,288 bytes. These are compression estimates, not a claim that the current server enables gzip.

One cached local Chromium desktop navigation measured DOMContentLoaded at 80ms and load at 92ms. This is a single warmed local observation, not a benchmark or an INP/Lighthouse score. Cold-load, container responsiveness and real-device measurements remain unmeasured.
