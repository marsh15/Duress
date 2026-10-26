# Duress demonstration

Tagline: **Put your integrations under pressure. Keep the proof.**

The [silent walkthrough](screenshots/duress-walkthrough.webm) and [screenshots](screenshots/) show the real application browsing saved results from an executed local browser journey. They are not a recording of hosted inference or a new experiment started by the capture script. The authored fixture provenance is visible. The separate M2 showcase screen remains labelled historical.

## Three-minute live storyline

1. **Start with the business contract.** A normal webhook should capture a payment and fulfil its order once. Run `uv run --python 3.12 duress run razorpay-fixed --scenario S1 --json` for the ordinary success control. HTTP 200 alone does not establish that business effect.
2. **Start an investigation.** On `/investigations`, select `razorpay-vulnerable` and Cached. Explain that bundled hypotheses are authored fixtures; citations and predictions are still validated, and actual execution establishes findings. A configured live provider would supply proposals through the same boundary.
3. **Show the result, not a model assertion.** Wait for Lifecycle Completed and business verdict FAIL. Open findings, then the S3/S5 scenario details: the exact parameterized IDs, concurrent deliveries, controlled provider rendezvous, trial counts, and invariant signature.
4. **Open the business evidence.** Follow a retained-state link. The copied SQLite tables contain multiple fulfilment rows for the affected order/payment. The supervisor settled the target before making that copy.
5. **Carry and replay the proof.** Open generated DNA, inspect ordering, faults, expectations, and violation rows, then download it. Replay against fresh vulnerable processes. The observed release record reproduced the same signature in 10/10 valid replay trials.
6. **Check the repair.** Verify the corrected target. The observed result re-established the control in 5/5 valid trials, then observed no failures in 20 valid candidate trials. Read the conditional statement; VERIFIED is bounded evidence for this reproducer.
7. **Explain a reduction that cannot remove a delivery.** Start bounded reduction. In this run, every removal violated the party-released rendezvous conditions. NOT_REDUCED retained the original DNA. Its separate fault-free characterization observed the same failure in 10/10 valid trials. This is empirical evidence on this host, not a global failure-rate claim.
8. **Show a successful removal separately.** The [latency reduction](release/2026-10-27/accepted-reduction/record.json) removed one of three deliveries, passed independent revalidation, and produced a separately downloadable artifact that replayed in a fresh home. Its claim remains bounded to admissible single-delivery removal.
9. **Expose the naive repair.** Compare fixes using Explore. S3 passed on naive_fix, but S5 failed: deduplicating an event ID did not deduplicate the business effect across different events. Corrected passed both. S9 passed all three; the nine observed cells agreed with the registered expectations.

## Reproduce the recording

Start the application against a home containing actual completed records. Capture only after operations terminate:

```bash
uv run --python 3.12 python scripts/capture_demo.py \
  --base-url http://127.0.0.1:8000 \
  --investigation <completed-investigation-id> \
  --reduction <completed-reduction-id> \
  --differential <completed-differential-id> \
  --verification <saved-verification-operation-id> \
  --video
```

The script only reads APIs and pages and captures their rendering. It refuses an incomplete investigation or a showcase record in place of an executed investigation. The output is a silent 1440×1000 walkthrough plus PNG screenshots. It does not create model responses, trials, verdicts, or terminal output. For a narrated submission, record the storyline above while using the running application; do not describe the saved-result walkthrough as live execution.

## Runtime and access notes

The isolated browser acceptance fixture raises its local operation quota to complete the entire journey. Public defaults remain three operations per IP per hour and thirty per IP per day; plan a public recording within that quota. Browser navigation does not cancel server-side operations. Enter an authorized token for exhaustive/live and replay/verification/reduction/comparison when configured.

Hosted inference and Render were not accessible in the release session. No public URL or hosted model result is claimed. [Release records](release/2026-10-27/) preserve the observed local results; [EVIDENCE.md](EVIDENCE.md) and [FINAL_RUNTIME.md](FINAL_RUNTIME.md) record checks and limitations.
