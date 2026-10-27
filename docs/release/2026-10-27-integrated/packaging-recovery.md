# Packaging recovery disposition — 27 October 2026

Source: `32844711721166a7a41690759517eeb3e0e412bb`. Independently reconstructed from retained output; this is not the missing delegated agent report. The delegated agent failed on account usage before writing REPORT.md. Its acceptance subprocesses completed.

- Wheel/source archive and clean outside-checkout installation: completed; 119 installed package files matched exactly. Installed `report showcase` and offline HTML export passed.
- Real PostgreSQL principal controls: all five accepted (flawed FAIL, corrected PASS, replay REPRODUCED, corrected VERIFIED, flawed candidate NOT_VERIFIED). Optional reduction completed REDUCED (three deliveries to two).
- Corrected nine-scenario suite: 45/45 valid PASS, zero inconclusive. Reduced artifact-only replay: 10/10 matching failures, zero inconclusive. Its exit 1 means reproduced failure, not an operational failure.
- Cleanup: zero leftover trial schemas; owned PostgreSQL container removed; no runtime credentials retained.
- Initial original Dockerfile attempts failed on dependency-download timeouts. A later retry of the original Dockerfile succeeded as `duress:beta-3284471`; this is distinct from the earlier fallback image.
- Actual successful image: 111 focused Linux tests and 25 HTTP/static/security checks passed, including token 401, hosted external execution 403, fonts/assets and genuine synthetic replay.
- Licensing-only wheel/source distribution rebuilt successfully; executable Python/frontend remained identical to accepted source. Current-head CI must separately check the final release commit.

Raw evidence remains under `/tmp/duress-final-packaging-r3/`; portable selected JSON/XML records are linked in this directory’s README. Earlier failed/interrupted/inconclusive experiments remain historical and are not relabelled. No full duplicate packaging run was started during recovery. Public hosting, fresh Alan skill invocation and organizer acceptance are separate unverified gates.
