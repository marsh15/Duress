# Integrated developer beta acceptance — 27 October 2026

Accepted executable source: `32844711721166a7a41690759517eeb3e0e412bb`.
Subsequent release changes add licensing, evidence, documentation, the Alan skill procedure
and demonstration media. They do not change package Python, frontend assets or executor
semantics. Existing historical evidence remains intact. These results have separate scopes;
they are not summed into a fictional single test total.

## Executed gates

- [Actions run 40473322427](https://github.com/marsh15/Duress/actions/runs/40473322427):
  both jobs passed. Python 3.12 full remote regression **770 passed, 30 browser opt-in
  skipped**, zero failures/errors; wheel/sdist and independent PostgreSQL controls passed.
  [Downloaded JUnit](remote-regression.xml).
- Local Python 3.12.12: **579 passed**, 40.25 seconds, focused unit/web/DNA regression;
  [JUnit](python-focused.xml). The existing Starlette/httpx deprecation warning remains.
- Chromium: **30 passed**, 533.22 seconds; [JUnit](browser.xml). Real investigation,
  state evidence, DNA export/import, replay, verification, reduction, differential,
  tamper rejection, keyboard/focus and accessibility workflows were exercised.
- Actual Linux container built with the repository's original Dockerfile:
  `duress:beta-3284471`; **111 focused tests passed**, 15.62 seconds;
  [JUnit](linux-focused.xml). **25 HTTP/static/security checks passed**, including all
  frontend bytes/fonts, missing/invalid-token 401, hosted external-execution 403 and
  actual synthetic replay. Earlier clean Docker builds failed dependency-download
  timeouts; the final retry succeeded. No fallback image is represented as this build.
- Fresh installed wheel outside the checkout: all five [PostgreSQL controls](installed-wheel-controls.json)
  passed: flawed FAIL, corrected PASS, replay REPRODUCED, candidate VERIFIED, flawed
  candidate NOT_VERIFIED. Reduction also returned REDUCED, three deliveries to two.
- Corrected independent PostgreSQL application: **45/45 valid PASS** across nine
  scenarios, zero inconclusive. Exported reduced artifact replayed in an empty store:
  **10/10 matching failures**, zero inconclusive. [Exact summary](external-acceptance.json),
  [original DNA](failure-dna.json), [reduced DNA](reduced-failure-dna.json).
- Standalone `duress report showcase` from outside the checkout passed after the CLI
  resource-root correction. The real external stored-report export also passed.
  Offline reports retain embedded fonts, license and raw evidence; no app-relative
  controls are presented as functional.
- Credential scan of final installed-wheel acceptance found no runtime credentials;
  the dedicated PostgreSQL database had zero remaining trial schemas before cleanup.

The installed application bundle used during execution was `d7c646c`. Its later
`f3598c1` commit adds only MIT licensing/metadata and preserves its implementation and
history. Both Duress distributions include MIT metadata and the license notice.

## Visual and interaction acceptance

[Audit data](responsive-axe.json) covers 14 populated routes, both themes and nine widths:
320, 375, 390, 430, 768, 1024, 1280, 1440 and 1600px. All **252 layout states** have no
page overflow. The **28 route/theme axe scans** have no serious/critical findings.
Technical tables scroll within their containers. [320px investigation](investigation-320px.png).
CSS 200% reflow was checked; this is not a claim of native OS browser zoom certification.

The final corrections wrap long scenario identifiers and explanations, preserve valid
trial quotas while allowing budgeted invalid retries in browser assertions, fail CI
artifact uploads closed if sanitization fails, and resolve CLI resources from an installed
wheel while retaining runtime records in the invoking workspace.

[Static payload measurements](frontend-assets.json) are raw on-disk bytes, not compressed
transfer estimates or Lighthouse/INP scores. Earlier single warmed local observations are
documented in the design report; no representative mobile-device benchmark is claimed.
Physical-device and full screen-reader testing remain unverified. Chromium keyboard,
focus management, both-theme axe and reduced-motion checks passed in the browser suite.

## Failed attempts and limits

The first browser run had 29 passes and an overly exact 30-attempt assertion; one invalid
attempt had correctly been retried. The next run's new 320px check exposed actual summary
overflow; wrapping was fixed before the final 30-test pass. An overloaded local full run
was interrupted at 153 passes/30 skips and is **not** counted as full acceptance. The
complete current-code full gate is the remote result above. Earlier installed-wheel
experiments with preparation timeouts remain separately recorded; final fresh acceptance
above did not relabel or discard inconclusive trials.

Public synthetic hosting is separate from local external-project execution. The owner deployed the existing Free service; [public acceptance](../2026-10-27-public/README.md) records the verified URL, actual investigation, billing boundaries and remaining public-operation checks. Genuine historical Alan execution
is [documented](../../openfire/alan.md); fresh skill invocation and Duress internal hosted
inference remain unverified. No PyPI publication or organizer acceptance is claimed.

## Recovery and publication checks

The interrupted delegated verifier did not write its requested `/tmp` report because its account usage limit ended the agent. Its subprocesses completed; the release owner independently read the acceptance outputs and completed the original-Dockerfile build and Linux checks. The [reconstructed packaging disposition](packaging-recovery.md) records that distinction. No duplicate full packaging suite was run.

A final OSV advisory query using pip-audit on the pinned default runtime plus PostgreSQL extra reported no known vulnerabilities. [Advisory response](runtime-advisories.json). This is a dependency advisory snapshot, not container OS certification.

The owner approved MIT terms for both owned applications, public repository visibility and the reviewed personal Git metadata. The final publication audit remains a bounded scan; historical disclosures were accepted, not silently removed. Current README screenshots and the demo have [media provenance](../../openfire/media-review.md).
