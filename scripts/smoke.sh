#!/usr/bin/env bash
# Hosted or local smoke test. It only calls the public API, so it behaves the
# same against Docker and Render. Uses the project's httpx instead of curl
# because the release image and some hosts do not ship curl.
#
#   scripts/smoke.sh http://127.0.0.1:8000 [DNA_ID]
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${1:?usage: smoke.sh <base-url> [dna_id]}"
DNA="${2:-}"
export BASE DNA

uv run python - <<'PY'
import json, os, sys
import httpx

base = os.environ["BASE"].rstrip("/")
dna = os.environ.get("DNA") or ""
token = os.environ.get("DURESS_DEMO_TOKEN")
headers = {"X-Duress-Token": token} if token else {}

def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)

with httpx.Client(base_url=base, timeout=30.0) as client:
    print("== health ==")
    try:
        health = client.get("/api/health")
    except httpx.HTTPError as exc:
        fail(f"health endpoint unreachable: {exc}")
    if health.status_code != 200:
        fail(f"health returned {health.status_code}")
    print(json.dumps(health.json()))
    if health.json().get("status") != "ok":
        fail("health is not ok")

    print("== targets ==")
    targets = client.get("/api/targets")
    if targets.status_code != 200:
        fail(f"targets returned {targets.status_code}")
    ids = {item["target_id"] for item in targets.json().get("targets", [])}
    for needed in ("razorpay-vulnerable", "razorpay-fixed"):
        if needed not in ids:
            fail(f"{needed} missing from the registry")
    print(sorted(ids))

    if dna:
        print(f"== dna {dna} ==")
        document = client.get(f"/api/dna/{dna}")
        if document.status_code != 200:
            fail(f"DNA {dna} not found ({document.status_code})")
        print(f"signature {document.json()['reproducer']['failure_signature']}")

        print("== replay ==")
        replay = client.post(f"/api/dna/{dna}/replay", json={}, headers=headers)
        if replay.status_code != 200:
            fail(f"replay returned {replay.status_code}: {replay.text[:300]}")
        print(json.dumps(replay.json()["outcome"]))

        print("== verify-fix ==")
        verify = client.post(f"/api/dna/{dna}/verify-fix", json={}, headers=headers)
        if verify.status_code != 200:
            fail(f"verify-fix returned {verify.status_code}: {verify.text[:300]}")
        print(json.dumps(verify.json()))

print(f"== smoke passed: {base} ==")
PY
