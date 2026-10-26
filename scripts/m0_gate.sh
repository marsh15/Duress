#!/usr/bin/env bash
# M0 acceptance gate. Used identically locally and inside the container.
#
#   scripts/m0_gate.sh                 # gate + measured experiments + EVIDENCE.md
#   scripts/m0_gate.sh --gates-only    # gate only (Docker / CI)
#
# Exit codes: 0 only when every gate passes. pytest exit codes 4 (bad path) and
# 5 (nothing collected) fail here as well.
set -euo pipefail
cd "$(dirname "$0")/.."

GATE_LABEL="${GATE_LABEL:-local}"
START=$SECONDS
GATES="tests/m0/test_e1_vulnerable_race.py tests/m0/test_e2_fixed_exactly_once.py tests/m0/test_e3_drain.py"

echo "== Duress M0 gate (${GATE_LABEL}) =="

collected=$(uv run pytest --collect-only -q $GATES 2>/dev/null | grep -c '::' || true)
if [ "${collected}" -ne 4 ]; then
    echo "FAIL: expected 4 gate tests (E1, E2[2/2], E2[5/5], E3), collected ${collected}"
    exit 1
fi
echo "collected ${collected} gate tests"

echo "-- target suite (both variants, sequential controls, integrity classifier)"
uv run pytest targets/razorpay_ref/tests -q

echo "-- gate suites"
uv run pytest $GATES -q -s

uv run python scripts/m0_evidence.py record-gate \
    --label "${GATE_LABEL}" \
    --collected "${collected}" \
    --seconds "$((SECONDS - START))"

if [ "${1:-}" != "--gates-only" ]; then
    echo "-- measured experiments (E4-E6, not gates)"
    uv run pytest tests/m0/test_e4_disconnect.py tests/m0/test_e5_rates.py tests/m0/test_e6_timing.py -q -s
    uv run python scripts/m0_evidence.py render
    echo "-- docs/EVIDENCE.md regenerated"
fi

echo "== M0 gate passed (${GATE_LABEL}) in $((SECONDS - START))s =="
