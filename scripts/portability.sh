#!/usr/bin/env bash
# Prove that portable DNA survives an independent home: build in A, copy only
# the JSON artifact into empty B, then replay and verify there.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
HOME_A="${TMP}/home-a"
HOME_B="${TMP}/home-b"
mkdir -p "${HOME_A}" "${HOME_B}"

echo "== build in isolated home A =="
DURESS_RECORD_MEASUREMENTS= DURESS_HOME="${HOME_A}" uv run duress dna build razorpay-vulnerable \
    --scenario S3 --copies 2 --fault rendezvous --json > "${TMP}/dna.json"
DNA_ID="$(uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["dna_id"])' "${TMP}/dna.json")"
echo "observed dna_id ${DNA_ID}"

echo "== copy only the portable artifact into isolated home B =="
mkdir -p "${HOME_B}/dna"
cp "${TMP}/dna.json" "${HOME_B}/dna/${DNA_ID}.json"
FILE_COUNT="$(find "${HOME_B}" -type f | wc -l | tr -d ' ')"
if [ "${FILE_COUNT}" -ne 1 ]; then
    echo "FAIL: home B contains files beyond the copied DNA artifact" >&2
    exit 1
fi
echo "home B initially contains one file: dna/${DNA_ID}.json"

echo "== replay from home B =="
set +e
DURESS_RECORD_MEASUREMENTS= DURESS_HOME="${HOME_B}" uv run duress replay "${DNA_ID}" --json > "${TMP}/replay.json"
REPLAY_STATUS=$?
set -e
uv run python - "${TMP}/replay.json" "${REPLAY_STATUS}" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
status = int(sys.argv[2])
outcome = payload.get("outcome")
if outcome != "REPRODUCED" or status != 1:
    raise SystemExit(f"FAIL: expected REPRODUCED (exit 1), observed {outcome!r} (exit {status})")
print(f"PASS replay from home B: {outcome} (exit {status})")
PY

echo "== verify-fix from home B =="
set +e
DURESS_RECORD_MEASUREMENTS= DURESS_HOME="${HOME_B}" uv run duress verify-fix "${DNA_ID}" --json > "${TMP}/verify.json"
VERIFY_STATUS=$?
set -e
uv run python - "${TMP}/verify.json" "${VERIFY_STATUS}" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
outcome = payload.get("outcome")
status = int(sys.argv[2])
if outcome != "VERIFIED" or status != 0:
    raise SystemExit(f"FAIL: expected VERIFIED (exit 0), observed {outcome!r} (exit {status})")
print(f"PASS verify-fix from home B: {outcome} (exit {status})")
PY

echo "== portability passed =="
