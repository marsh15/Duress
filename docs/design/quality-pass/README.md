# Final visual and usability pass

Reviewed the existing evidence-instrument design and corrected confirmed problems.
FastAPI, Jinja, HTMX, vanilla JavaScript, the token palette and trusted execution engine
remain in place. No oracle, scenario, invariant or artifact encoding was changed.

## Confirmed issues, ordered by impact

1. **P1 — False empty findings on the overview.** The compact investigation-index API
   intentionally omits findings. The UI treated that omission as an empty result even
   when the saved investigation contained a failure. The home page now renders retained
   findings from canonical saved records, with links to the supporting investigation.
   Recent records also show the actual business verdict and trial count instead of
   the uninformative “Saved record” label.
2. **P1 — Lost focus with nested mobile navigation.** Escape closed the command palette
   and its underlying drawer together. It now dismisses only the top dialog, restoring
   focus to the drawer search trigger. A second Escape closes navigation. Project
   commands are deduplicated by destination. The palette exposes combobox/listbox
   selection and the active option to assistive technology. The compact top-bar search
   button also has an explicit accessible name when mobile CSS hides its text.
3. **P2 — Verdict buried below mobile section links.** Investigation and DNA section
   indexes now collapse to “On this page” on small screens, while desktop/tablet retain
   direct links. The verdict/signature remains immediately reachable. Indexes rebuild
   after persisted progress swaps add evidence sections.
4. **P2 — Returning users still saw the full onboarding block.** Registered workspaces
   now get a compact continuation message and an Open projects action, bringing actual
   records higher on the page. The first-run guide remains available and unchanged.
5. **P2 — Diagnostic fixtures obscured the main targets.** Reference applications appear
   first in target and comparison choices. Diagnostic profiles are explicitly labelled,
   and Targets has a discoverable New investigation action. This ordering is presentation
   only; the registry and available execution choices are unchanged.
6. **P2 — Comparison context was lost during horizontal scrolling.** Scenario labels
   stay pinned while the implementations scroll. A visible scroll hint explains the
   interaction on narrow screens. Observed verdicts use their semantic styles; expected
   verdicts and agreement remain separate. Manual mobile scrolling confirmed the scenario
   column stays at x=31px while the table scrolls through its full 352px range.
7. **P2 — Evidence/form controls lacked consistent geometry.** Timing inspection tracks
   now use restrained token borders and vertically centred bars instead of oversized
   native button borders. Project labels and inputs align in a clear vertical flow;
   external result actions have usable separation. External scenario/trial statuses use
   the authoritative PASS/FAIL/INCONCLUSIVE semantic classes. Appearance switches apply
   foreground and background roles together to avoid transient low-contrast interpolation;
   hover feedback remains immediate, with a short border transition. Non-reduction claims remain
   neutral instead of receiving a success-colored treatment.
8. **P2 — Portable report links required a live application.** Standalone investigation
   reports retain artifact IDs as references with an explanation, rather than offering
   relative DNA links that fail when the report is opened elsewhere. In-app links remain
   functional. Report generation still starts no execution.

## Real-browser review coverage

The running application was inspected at **1440×1000 desktop**, **768×1000 tablet**, and
**390×844 mobile**, in **dark and light** appearance. Additional automated checks cover
320, 430, 1024 and 1600px. Twenty initial application views/states were captured across
all six combinations, including deeper timing and business-state views. Twelve modified
views/states were recaptured after corrections and theme transitions settled: **72 final
captures**, with no full-page horizontal overflow. Technical tables remain independently
scrollable.

Reviewed views: overview; investigation creation/history; completed investigation with
findings, hypotheses, scenarios and progress; DNA library/detail; projects/list/detail;
registered external investigation and before/after state; targets; onboarding guide;
replay; correction verification; reduction; comparison setup/result; retained SQLite
snapshot; missing-record error; component reference; command palette and navigation.
Portable reference and external reports were also inspected at the three sizes. Reports
are static dark documents; their embedded light-token rendering was checked through
browser inspection, not an invented report theme-switching control.

### Execution provenance

The pre-existing cached investigation `inv-20261027T044209-843af6` and its DNA
`0edb63c480dbc29b` were reused. This pass executed real bounded operations:

- Replay `34a397ea0f1547ce`: REPRODUCED, 10 failures / 10 valid trials.
- Verification `469bc1ef48374728`: VERIFIED; control 5/5 same-signature failures,
  candidate 0/20 valid trials with the same failure.
- Reduction `red-29a614f131fc`: NOT_REDUCED; no admissible removal, original DNA retained.
- Comparison `diff-137feca89166`: 9 cells, 9 agreements, 0 inconclusive observations.
- A temporary, unmodified generated starter was initialized and registered as
  `visual-review`. Its real `duplicate-activation` investigation `i59f1c33e51384967`
  completed PASS in 5 valid trials, with an observed 0→1 activation-row change.

No result was fabricated or modified for a screenshot. The starter, operation records
and raw trial state remain outside the repository; only authentic captures are committed.
No hosted model inference is implied.

## Final screenshots

Each matrix contains desktop, tablet and mobile columns; dark above, light below.
These sheets only resize and arrange actual browser screenshots. Standalone PNGs are
untouched captures.

- [Overview matrix](overview-matrix.png) · [Full desktop dark](overview-desktop-dark.png)
- [Investigation matrix](investigation-matrix.png) · [Creation/history](investigations-matrix.png)
- [DNA matrix](dna-matrix.png) · [Full mobile light](dna-mobile-light.png) · [Before mobile light](before-dna-mobile-light.png)
- [Target matrix](targets-matrix.png)
- [Project matrix](project-matrix.png) · [Project actions](project-actions-matrix.png)
- [External investigation](external-state-matrix.png) · [Business-state comparison](business-state-matrix.png)
- [Comparison matrix](comparison-matrix.png) · [Setup](comparison-form-matrix.png) · [Mobile scrolled](comparison-mobile-scrolled.png)
- [Measured timing](attempt-timing-matrix.png) · [Full desktop](attempt-timing-desktop-dark.png)
- [Mobile command palette](command-palette-mobile.png)
- [Portable report](portable-report-matrix.png) · [Offline artifact references](portable-report-artifact-references.png) · [External report](external-report-matrix.png)

## Validation

- Focused server/template checks: **44 passed** before the final portable-report refinement;
  **30 passed** in its affected report, evidence and template checks afterward.
- Python 3.12 regression command: `uv run --python 3.12 pytest -q tests/unit tests/integration/test_web_api.py tests/integration/test_dna_journey.py` — **578 passed**.
- Full Chromium command: `DURESS_BROWSER_TESTS=1 uv run pytest -q tests/browser` —
  **30 passed in 205.34s**. It includes the actual investigation → download → replay →
  verification → reduction → comparison journey, tamper rejection and error cases.
- Additional native-browser axe review covered **16 populated routes × both themes ×
  three viewport sizes** (96 states). The mobile icon-only search name and theme-switch
  contrast defects were corrected and rechecked; no serious/critical violations remain
  in those reviewed states. The established palette was preserved.
- Four focused browser checks also pass after the report refinement.
- Keyboard checks cover active-option announcements, nested Escape/focus restoration,
  section navigation, persisted theme preference, filtering and reduced motion.
- `node --check`, `git diff --check` and the wheel build pass.
- Asset budgets remain satisfied: JavaScript including HTMX **99,835 bytes**; CSS
  **80,783 bytes**; fonts **140,664 bytes**. No frontend runtime dependency was added.

No unresolved P0/P1/P2 defect was observed in the reviewed states. This is a Chromium,
keyboard and automated-axe review, not a physical-device or full screen-reader certification.
The existing Starlette/httpx TestClient deprecation warning remains unrelated to these fixes.
