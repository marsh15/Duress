# Public release acceptance — 27 October 2026

**VERIFIED PUBLIC DEPLOYMENT:** https://duress.onrender.com

Existing service `srv-dbga5mvb9u1s73cn8h60` was preserved. The owner manually deployed merged main `344db907adfb61519cab7023ce6db3772dcc3824`. GitHub's deployment record identifies that SHA, and the publicly served workspace CSS matches it exactly. A fresh build of that exact commit with the original Dockerfile also passed its focused health/UI/CSS/guide startup probe. No duplicate service, database, disk, worker or paid upgrade was created.

## Actual Free-service operation

The owner confirms Free compute and review of Billing; the release agent cannot independently inspect the account. The reported $50 credits, eligibility/expiry, current charges and future financial exposure are **not independently verified**. Paid resources remain unauthorized. Free compute is not a universal $0 billing cap; see the [cost/deployment handoff](../../openfire/RENDER_HANDOFF.md).

One public cached investigation, `inv-20261027T134459-5dffbe`, completed in **330.72 seconds** with business verdict **FAIL**: successful detection of the intended synthetic vulnerability. There was no automatic investigation retry. It used **31 attempts: 30 valid and one INCONCLUSIVE attempt** (`harness:target_unavailable`) retried within the existing budget. S1 and S2 met five-valid-PASS quotas; S3 and S5 each exposed duplicate fulfilment in 5/5 valid trials. Characterization produced DNA `0edb63c480dbc29b`. The operation completed within its 440-second wall cap, with no recorded operation errors. [Retained record](investigation.json), [summary](status.json), [downloaded DNA](failure-dna.json).

This demonstrates the bounded workload on this warmed Free instance. It does not establish sustained load/capacity, representative cold-start time or memory headroom. Container memory/CPU metrics, restart history and account billing were inaccessible. No universal performance or uptime claim is made. Do not upgrade without new explicit approval and credit/billing verification.

## Public smoke

- Health, redesigned overview, investigation list/detail, historical showcase, targets, guide, Failure DNA and retained business-state evidence returned HTTP 200.
- All 11 checked CSS/JS/Geist font resources loaded with HTTP 200 in the real browser; fonts loaded. Dark/light at desktop 1440px and mobile 390px passed the focused layout/appearance smoke without horizontal page overflow.
- Public external-project pages/API refuse non-local access with 403. Unauthenticated external-operation, replay and correction writes return 401. Authenticated hosted-external refusal was separately exercised in the accepted container evidence; no private demo token was accessed here.
- Public replay and correction verification are **pending owner operation with the private token** at this checkpoint. Local/Linux/CI replay and correction evidence already passed; these are separate scopes.

[Browser assets](browser-smoke.json), [four layout states](browser-layout-smoke.json), [HTTP/security checks](http-security-smoke.json), [deployed asset hash](deployed-asset.json).

## Main and media

[Main CI 40511107747](https://github.com/marsh15/Duress/actions/runs/40511107747) passed both jobs on `344db907`: 770 Python tests passed, 30 browser opt-in skipped, zero failures/errors. Clean-wheel installation, independent PostgreSQL controls, credential sanitization and evidence upload all passed. [JUnit](main-regression.xml), [job/step results](main-ci.json). Earlier evidence remains in the integrated acceptance directory.

The repository, MIT terms, README images and editable architecture were checked without authentication. The public 90-second player completed full anonymous playback with no media error and its MP4 matched the reviewed local file. A separate 22-second captioned launch clip uses real saved UI evidence and original quiet music. Both preserve the distinction between saved execution, authored cached hypotheses and historical Alan contribution. Fresh Alan skill invocation remains unverified.

Runtime `/tmp/duress` storage is ephemeral and may disappear after idle spin-down, restart or deploy. These exported records preserve this actual acceptance separately. Historical bundled showcase evidence remains available; no paid persistence was added. The final media/documentation commit uses Render's documented `[skip render]` marker to preserve the live executor and current evidence; it does not skip GitHub CI. Public deployment SHA and later documentation HEAD may therefore differ without a runtime-source change.

Final changed-file Gitleaks scanning found one reviewed non-credential: the 64-hex cached model+prompt SHA256 at `model.calls[0].key`. Source `cache_key()` computes that identifier; it is not a provider token. No real credential was confirmed. [Scan disposition](publication-scan.json).
