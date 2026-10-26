#!/usr/bin/env bash
# Prove that a DNA is portable: build it in one home, then replay and verify it
# from a second home that contains nothing but the artifact.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "== build (home A) =="
DURESS_HOME="${TMP}/a" uv run duress dna build razorpay-vulnerable \
    --scenario S3 --copies 2 --fault rendezvous --json > "${TMP}/dna.json"
DNA_ID=$(uv run python -c "import json,sys;print(json.load(open('${TMP}/dna.json'))['dna_id'])")
echo "dna_id ${DNA_ID}"

echo "== move only the artifact (home B) =="
mkdir -p "${TMP}/b/dna"
cp "${TMP}/dna.json" "${TMP}/b/dna/${DNA_ID}.json"
ls "${TMP}/b/dna"

echo "== replay from the clean home =="
set +e
DURESS_HOME="${TMP}/b" uv run duress replay "${DNA_ID}"
REPLAY=$?
set -e
[ "${REPLAY}" -eq 1 ] || { echo "expected REPRODUCED (exit 1), got ${REPLAY}"; exit 1; }

echo "== verify-fix from the clean home =="
DURESS_HOME="${TMP}/b" uv run duress verify-fix "${DNA_ID}"

echo "== portability passed =="
