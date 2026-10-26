#!/usr/bin/env bash
# Public read-only checks, plus an explicitly requested portable DNA replay and
# verify-fix. The script never starts an investigator or prints demo secrets.
#
#   scripts/smoke.sh <base-url>
#   DURESS_DEMO_TOKEN=... scripts/smoke.sh <base-url> --dna showcase/<id>.json
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${1:?usage: smoke.sh <base-url> [--dna <dna-file>]}"
DNA_FILE=""
if [ "$#" -gt 1 ]; then
    if [ "$#" -ne 3 ] || [ "$2" != "--dna" ]; then
        echo "usage: smoke.sh <base-url> [--dna <dna-file>]" >&2
        exit 2
    fi
    DNA_FILE="$3"
    if [ ! -f "$DNA_FILE" ]; then
        echo "FAIL: DNA file does not exist: $DNA_FILE" >&2
        exit 2
    fi
fi

DURESS_SMOKE_BASE_URL="$BASE_URL" DURESS_SMOKE_DNA_FILE="$DNA_FILE" uv run python - <<'PY'
import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx


base = os.environ["DURESS_SMOKE_BASE_URL"].rstrip("/")
dna_path = os.environ.get("DURESS_SMOKE_DNA_FILE") or ""
parsed = urlsplit(base)
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    print("FAIL: base URL must be an absolute http(s) URL", file=sys.stderr)
    raise SystemExit(2)
if parsed.username or parsed.password or parsed.query or parsed.fragment:
    print("FAIL: put credentials in environment variables, not in the URL", file=sys.stderr)
    raise SystemExit(2)

token = os.environ.get("DURESS_DEMO_TOKEN")
operation_headers = {"X-Duress-Token": token} if token else {}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def checked_json(client: httpx.Client, path: str, label: str) -> dict:
    try:
        response = client.get(path)
    except httpx.HTTPError as exc:
        fail(f"{label} request failed ({type(exc).__name__})")
    if response.status_code != 200:
        fail(f"{label} returned HTTP {response.status_code}")
    try:
        result = response.json()
    except ValueError:
        fail(f"{label} did not return JSON")
    if not isinstance(result, dict):
        fail(f"{label} JSON was not an object")
    return result


def checked_page(client: httpx.Client, path: str, label: str) -> str:
    try:
        response = client.get(path)
    except httpx.HTTPError as exc:
        fail(f"{label} request failed ({type(exc).__name__})")
    if response.status_code != 200:
        fail(f"public page {label} returned HTTP {response.status_code}")
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        fail(f"public page {label} returned content-type {content_type or '(missing)'}")
    print(f"PASS public page {label} (HTTP 200, HTML)")
    return response.text


class PageLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


with httpx.Client(base_url=base, timeout=45.0, follow_redirects=False) as client:
    print("== public health API ==")
    health = checked_json(client, "/api/health", "health")
    if health.get("status") != "ok":
        fail("health status was not ok")
    print(f"PASS health (version {health.get('version', 'not reported')})")

    print("== public target registry ==")
    targets_response = checked_json(client, "/api/targets", "targets")
    targets = targets_response.get("targets")
    if not isinstance(targets, list):
        fail("targets response did not contain a targets list")
    target_ids = {
        item.get("target_id") for item in targets if isinstance(item, dict)
    }
    for required in ("razorpay-vulnerable", "razorpay-fixed"):
        if required not in target_ids:
            fail(f"target registry is missing {required}")
    print("PASS targets: razorpay-vulnerable, razorpay-fixed")

    print("== public pages ==")
    home_html = checked_page(client, "/", "home")
    links = PageLinks()
    links.feed(home_html)
    investigation_pages = sorted(
        {
            href.split("?", 1)[0]
            for href in links.hrefs
            if href.startswith("/investigations/") and "/progress" not in href
        }
    )
    dna_pages = sorted(
        {
            href.split("?", 1)[0]
            for href in links.hrefs
            if href.startswith("/dna/") and not href.endswith("/download")
        }
    )
    if not investigation_pages:
        fail("home page did not link to a public investigation detail page")
    if not dna_pages:
        fail("home page did not link to a public DNA detail page")
    checked_page(client, investigation_pages[0], "investigation detail")
    checked_page(client, dna_pages[0], "DNA detail")

    dna_document = None
    dna_id = None
    if dna_path:
        try:
            dna_document = json.loads(Path(dna_path).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"DNA file could not be read as JSON ({type(exc).__name__})")
        if not isinstance(dna_document, dict) or not isinstance(dna_document.get("reproducer"), dict):
            fail("DNA file is not a portable Failure DNA document")
        dna_id = dna_document.get("dna_id")
        if not isinstance(dna_id, str) or not dna_id:
            fail("DNA file has no dna_id")

    if dna_document is not None:
        portable_page = f"/dna/{quote(dna_id, safe='')}"
        if portable_page not in dna_pages:
            checked_page(client, portable_page, "portable DNA detail")
        print("== portable DNA replay ==")
        if health.get("token_required") and not token:
            fail("this server requires DURESS_DEMO_TOKEN for --dna operations")
        provenance = dna_document.get("provenance")
        if provenance is not None and not isinstance(provenance, dict):
            fail("DNA provenance must be an object")
        target_id = (provenance or {}).get("target_id") or "razorpay-vulnerable"
        try:
            replay = client.post(
                "/api/dna/replay",
                json={"dna": dna_document, "target_id": target_id},
                headers=operation_headers,
            )
        except httpx.HTTPError as exc:
            fail(f"portable replay request failed ({type(exc).__name__})")
        if replay.status_code != 200:
            fail(f"portable replay returned HTTP {replay.status_code}")
        try:
            replay_outcome = replay.json().get("outcome", {}).get("outcome")
        except (ValueError, AttributeError):
            fail("portable replay response was not a valid outcome")
        if replay_outcome != "REPRODUCED":
            fail(f"portable replay outcome was {replay_outcome!r}, expected 'REPRODUCED'")
        print("PASS portable payload replay: REPRODUCED")

        # Verify-fix is keyed by the server's stored DNA id. Confirm the exact
        # uploaded artifact is present before running that operation.
        try:
            stored = client.get(f"/api/dna/{quote(dna_id, safe='')}")
        except httpx.HTTPError as exc:
            fail(f"DNA lookup before verify-fix failed ({type(exc).__name__})")
        if stored.status_code != 200:
            fail(
                "portable replay succeeded, but this service does not have the same DNA id stored; "
                "verify-fix requires the bundled artifact to be present"
            )

        print("== verify-fix ==")
        try:
            verify = client.post(
                f"/api/dna/{quote(dna_id, safe='')}/verify-fix",
                json={},
                headers=operation_headers,
            )
        except httpx.HTTPError as exc:
            fail(f"verify-fix request failed ({type(exc).__name__})")
        if verify.status_code != 200:
            fail(f"verify-fix returned HTTP {verify.status_code}")
        try:
            verify_outcome = verify.json().get("outcome")
        except (ValueError, AttributeError):
            fail("verify-fix response was not a valid outcome")
        if verify_outcome != "VERIFIED":
            fail(f"verify-fix outcome was {verify_outcome!r}, expected 'VERIFIED'")
        print("PASS verify-fix: VERIFIED")

print("== smoke passed ==")
print("No cached or live investigation was started; only public reads and explicitly requested DNA operations ran.")
PY
