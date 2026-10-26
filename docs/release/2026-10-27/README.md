# Integrated release observations

These files are exact saved records from a passing **real local browser journey**, using Python 3.12 and the locked dependencies. The journey invoked the application API and trusted services; no execution endpoint was mocked. Runtime records were copied byte-for-byte from the isolated test home. `records-manifest.json` records their original paths and SHA-256 digests. They are new observations, separate from the historical M0–M4 measurement files and the M2 showcase.

| Record | Observation |
|---|---|
| [Investigation](investigation.json) | `inv-20261026T201423-ce3e28`: completed, FAIL, cached authored-fixture provenance, H1/H2 confirmed through execution, 30 trials, one generated DNA; duration 25.26 s |
| [Failure DNA](failure-dna.json) | `0edb63c480dbc29b`, duplicate fulfilment on `order:A`, exact three-delivery rendezvous schedule, valid reproducer digest |
| [Replay](replay.json) | Preflight passed; same failure REPRODUCED in 10/10 valid trials, no inconclusive trials |
| [Verification](verification.json) | VERIFIED: vulnerable control 5/5 same-signature failures; corrected candidate 20 valid trials, zero same-signature or other failures, zero inconclusive trials |
| [Reduction](reduction.json) | NOT_REDUCED, no admissible removals, original remains primary; required fault-free characterization observed 10/10 failures |
| [Differential](differential.json) | 9/9 agreeing cells across S3/S5/S9 and vulnerable/naive_fix/fixed; 45 valid trials, no disagreements or inconclusive cells |

The reduction's unchanged schedule is meaningful: removing a delivery would change the rendezvous party conditions. No smaller artifact or global minimality is asserted. The independent reducer regression also exercises accepted reduced artifacts and failed revalidation; those automated checks are reported in the release test evidence, not inferred from this NOT_REDUCED example.

The [screenshots](../../screenshots/) and [silent walkthrough](../../screenshots/duress-walkthrough.webm) browse these completed records in the actual application. The separate `recorded-evidence.png` shows the clearly labelled historical M2 showcase. The capture script starts no new experiment.

## Replay the portable artifact in an empty home

```bash
release_home="$(mktemp -d)"
mkdir -p "$release_home/dna"
cp docs/release/2026-10-27/failure-dna.json "$release_home/dna/0edb63c480dbc29b.json"
DURESS_HOME="$release_home" uv run --python 3.12 duress replay 0edb63c480dbc29b --json
DURESS_HOME="$release_home" uv run --python 3.12 duress verify-fix 0edb63c480dbc29b --candidate razorpay-fixed --json
```

REPRODUCED returns exit code 1 deliberately; VERIFIED returns 0. The artifact contains the reproduction conditions, not a promise of correctness on other schedules or environments. Digests detect alteration, not authenticity. No runtime API credentials are included.

See [executed gates](../../EVIDENCE.md), [runtime verification](../../FINAL_RUNTIME.md), and [demo instructions](../../DEMO.md) for exact commands and external prerequisites.

## Additional final acceptance

[Clean-clone CLI records](clean-cli/) include normal S1 PASS, exported-artifact replay and verification, cached investigation, rendezvous reduction, and the longer 180-valid-trial matrix with 9/9 agreement. [Accepted reduction](accepted-reduction/) preserves a separate latency experiment that genuinely reduced from three deliveries to two after ten revalidation trials. Its browser-downloaded artifact independently reproduced in ten valid trials; it does not replace or erase the rendezvous NOT_REDUCED result.

[Container observations](container/summary.json) cover final image `53f47629466f…`, Linux workflows and a real shutdown/restart cancellation. [Slow measurements](slow-measurements/) retain new E4/E5/E6 data separately from historical files. [Populated record accessibility](real-record-accessibility.json) and accepted-reduction browser checks cover actual data, including mobile widths. Failed browser harness runs are retained as compressed original JUnit, with corrections and passing reruns explained in `EVIDENCE.md`.
