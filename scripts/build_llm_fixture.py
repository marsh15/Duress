"""Write the committed cached-mode fixtures under ``fixtures/llm/``.

These responses are AUTHORED by the maintainers, not produced by a model. They
exist so the public demo and the test suite can exercise cached mode without a
provider. Every entry says so in its provenance, the investigation record
copies that provenance, and the key uses the model name
``duress-authored-fixture`` so an authored response can never be confused with
a recorded live one.

Citations are computed from the current target source by searching for the
cited statements, so the fixture stays truthful. Any change to the source
bundle or the prompt changes the cache key, and cached mode then reports
``cache_miss`` until this script is re-run.

Usage: uv run python scripts/build_llm_fixture.py [target_id ...]
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from duress import investigator
from duress.llm import LlmCache, cache_key, render_prompt
from duress.orchestrator import FIXTURE_MODEL
from duress.profiles import ProfileRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ("razorpay-vulnerable", "razorpay-naive-fix", "razorpay-fixed")


def find_line(lines: list[str], needle: str, start: int = 1) -> int:
    for number in range(start, len(lines) + 1):
        if needle in lines[number - 1]:
            return number
    raise LookupError(needle)


def handler_hypotheses(relpath: str, lines: list[str]) -> list[dict]:
    """Two hypotheses about the variant's handler, cited by statement search."""
    insert = find_line(lines, "INSERT INTO fulfilments")
    try:
        read = find_line(lines, "SELECT status, amount, currency FROM payments")
    except LookupError:
        read = find_line(lines, "SELECT payment_id FROM fulfilments")
    start, end = sorted((read, insert))
    span = (start, min(end, start + 79))
    citation = {
        "file": relpath,
        "line_start": span[0],
        "line_end": span[1],
        "quote": lines[span[0] - 1].strip(),
    }
    return [
        {
            "id": "H1",
            "title": "Status check and fulfilment insert are not atomic for duplicate deliveries",
            "mechanism": (
                "The handler reads the payment and order status, awaits the provider, and only "
                "then inserts the fulfilment. Concurrent copies of the same event can all pass the "
                "status check before any of them commits, producing more than one fulfilment."
            ),
            "mechanism_kind": "check_then_act_race",
            "citations": [citation],
            "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
            "scenarios": [{"template_id": "S3", "params": {"copies": 3, "fault": "rendezvous", "latency_ms": 200}}],
        },
        {
            "id": "H2",
            "title": "Different events for the same payment race through the same window",
            "mechanism": (
                "payment.captured and order.paid carry different event ids but the same payment. "
                "Deduplicating by event id cannot stop them, so the same check-then-act window "
                "applies across the two events."
            ),
            "mechanism_kind": "check_then_act_race",
            "citations": [citation],
            "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
            "scenarios": [{"template_id": "S5", "params": {"fault": "rendezvous", "latency_ms": 200}}],
        },
    ]


def build(target_id: str, registry: ProfileRegistry, cache: LlmCache) -> Path:
    profile = registry.get(target_id)
    bundle = investigator.build_source_bundle(profile)
    messages = investigator.build_messages(profile, bundle, profile.roles)
    prompt = render_prompt(messages)
    key = cache_key(FIXTURE_MODEL, prompt)
    # Cite the handler that performs the fulfilment write: the variant's own,
    # or the one it delegates to (naive_fix reuses the vulnerable handler).
    handlers = sorted(
        (path for path in bundle.files if path.endswith("/handler.py")),
        key=lambda path: (not path.endswith(f"{profile.variant}/handler.py"), path),
    )
    relpath = next(
        path for path in handlers if any("INSERT INTO fulfilments" in line for line in bundle.files[path])
    )
    output = {"hypotheses": handler_hypotheses(relpath, bundle.files[relpath])}
    investigator.parse_model_output(json.dumps(output))  # never commit an invalid fixture
    for stale in cache.write_dir.glob("*.json"):
        try:
            provenance = json.loads(stale.read_text()).get("provenance") or {}
        except (OSError, ValueError):
            continue
        if provenance.get("source") == "authored_fixture" and provenance.get("target_id") == target_id:
            stale.unlink()
    entry = {
        "key": key,
        "model": FIXTURE_MODEL,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response": {"content": json.dumps(output, indent=2, sort_keys=True), "usage": {}},
        "provenance": {
            "source": "authored_fixture",
            "note": "Written by scripts/build_llm_fixture.py from the current target source; not model output.",
            "target_id": target_id,
            "bundle_sha256": bundle.sha256,
        },
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return cache.put(key, entry)


def main(argv: list[str]) -> int:
    registry = ProfileRegistry.discover(ROOT)
    directory = ROOT / "fixtures" / "llm"
    directory.mkdir(parents=True, exist_ok=True)
    cache = LlmCache([directory], write_dir=directory)
    for target_id in argv or DEFAULT_TARGETS:
        path = build(target_id, registry, cache)
        print(f"{target_id}: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
