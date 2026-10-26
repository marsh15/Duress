#!/usr/bin/env python3
"""Capture real, completed application records using the repository's browser tooling.

This is read-only: it never creates an investigation or invents demonstration data.
Run with uv run --python 3.12 python scripts/capture_demo.py --help.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--investigation", required=True, help="A real completed investigation ID")
    parser.add_argument("--reduction")
    parser.add_argument("--differential")
    parser.add_argument("--verification", help="Saved verify-fix operation ID")
    parser.add_argument("--output", type=Path, default=Path("docs/screenshots"))
    parser.add_argument("--video", action="store_true", help="Also record a silent walkthrough of these saved results")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=10) as client:
        def completed(path: str) -> dict:
            response = client.get(path)
            response.raise_for_status()
            record = response.json()
            if record.get("status") != "completed":
                raise SystemExit(f"Capture requires an actual completed record: {path}")
            return record

        investigation = completed(f"/api/investigations/{args.investigation}")
        if investigation.get("record_type") != "investigation" or not investigation.get("dna_ids"):
            raise SystemExit("Choose a newly executed investigation with generated DNA, not the showcase.")
        dna_id = investigation["dna_ids"][0]
        response = client.get(f"/api/dna/{dna_id}")
        response.raise_for_status()
        dna = response.json()
        if args.reduction:
            completed(f"/api/reductions/{args.reduction}")
        if args.differential:
            completed(f"/api/differential/{args.differential}")
    pages = [
        ("workbench.png", "/", None),
        ("investigations.png", "/investigations", None),
        ("investigation.png", f"/investigations/{args.investigation}", None),
        ("investigation-findings.png", f"/investigations/{args.investigation}", "#findings-title"),
        ("failure-dna.png", f"/dna/{dna_id}", None),
        ("recorded-evidence.png", "/investigations/showcase-m2", None),
    ]
    trial_id = dna.get("observations", {}).get("trial_id")
    if trial_id:
        pages.append(("business-state.png", f"/evidence/{trial_id}", None))
    if args.reduction:
        pages.append(("reduction.png", f"/reductions/{args.reduction}", None))
    if args.differential:
        pages.append(("comparison.png", f"/differential/{args.differential}", "#matrix-title"))
    if args.verification:
        pages.append(("verification.png", f"/operations/{args.verification}", None))
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        options = {"record_video_dir": str(args.output / "video"),
                   "record_video_size": {"width": 1440, "height": 1000}} if args.video else {}
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1, **options)
        page = context.new_page()
        for filename, route, anchor in pages:
            response = page.goto(base + route, wait_until="networkidle")
            if response is None or response.status != 200:
                raise SystemExit(f"Page unavailable: {route}")
            page.get_by_role("heading", level=1).wait_for()
            if anchor:
                page.locator(anchor).scroll_into_view_if_needed()
            page.screenshot(path=str(args.output / filename), animations="disabled")
            if args.video:
                page.wait_for_timeout(2000)
            print(f"Captured {filename} from {route}")
        video = page.video
        context.close()
        if video:
            video.save_as(str(args.output / "duress-walkthrough.webm"))
            video.delete()
            (args.output / "video").rmdir()
        browser.close()


if __name__ == "__main__":
    main()
