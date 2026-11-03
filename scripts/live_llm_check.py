#!/usr/bin/env python3
"""Run ONE live investigation against an OpenAI-compatible provider and save the result.

Configuration comes only from the environment:

    DURESS_LLM_BASE_URL   required, e.g. https://provider.example/v1
    DURESS_LLM_MODEL      required, the provider's model name
    DURESS_LLM_API_KEY    optional (some local servers need none)

Usage:
    uv run python scripts/live_llm_check.py --out live-check.json
    uv run python scripts/live_llm_check.py --target razorpay-vulnerable --wall-cap 900

Exit codes:
    0  live proposal accepted: the investigation ran in live mode with no fallback
    1  investigation completed but fell back to exhaustive (reason in the JSON)
    2  configuration missing or invalid; nothing was sent to any provider
    3  unexpected error, or the result would have contained the API key (not written)

The result JSON labels its evidence class. A loopback base URL (127.0.0.1,
localhost, ::1) is labelled ``mock-provider``; any other host is labelled
``external-provider``. Neither label is a claim about a particular hosted model.
This script is for the owner to run with a real key. It never runs in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "duress.live_llm_check/1"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
REQUIRED_ENV = ("DURESS_LLM_BASE_URL", "DURESS_LLM_MODEL")


def missing_env_message(env: dict[str, str]) -> str | None:
    missing = [name for name in REQUIRED_ENV if not (env.get(name) or "").strip()]
    if not missing:
        return None
    return (
        "live_llm_check: missing environment variable(s): "
        + ", ".join(missing)
        + "\n  set DURESS_LLM_BASE_URL and DURESS_LLM_MODEL (DURESS_LLM_API_KEY is optional)."
        + "\n  no request was sent to any provider."
    )


def evidence_class(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    return "mock-provider" if host in LOOPBACK_HOSTS else "external-provider"


def summarise(record: dict[str, Any]) -> dict[str, Any]:
    model = record.get("model") or {}
    calls = model.get("calls") or []
    return {
        "status": record.get("status"),
        "mode_effective": record.get("mode_effective"),
        "fallback_reason": record.get("fallback_reason"),
        "fallback_detail": record.get("fallback_detail"),
        "verdict": record.get("verdict"),
        "usable_hypotheses": record.get("usable_hypotheses"),
        "selected_scenarios": record.get("selected_scenarios", []),
        "model_calls": len(calls),
        "model_call_sources": [call.get("source") for call in calls],
        "model_response_source": model.get("response_source"),
        "model_budget": model.get("budget"),
        "model_provenance": model.get("provenance"),
    }


def run(args: argparse.Namespace, env: dict[str, str]) -> int:
    message = missing_env_message(env)
    if message:
        print(message, file=sys.stderr)
        return 2

    from duress import limits as limits_module
    from duress.llm import LlmConfig
    from duress.profiles import ProfileRegistry
    from duress.services import Workspace, investigate
    from duress.store import ensure_dirs

    config = LlmConfig.from_env(env)
    assert config is not None  # guarded by missing_env_message

    home = Path(tempfile.mkdtemp(prefix="duress-live-check-"))
    ensure_dirs(home)
    workspace = Workspace(
        root=ROOT,
        home=home,
        registry=ProfileRegistry.discover(ROOT),
        limits=limits_module.DEFAULT_LIMITS,
    )

    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    print(
        f"live_llm_check: one live investigation on {args.target} "
        f"(model={config.model}, host={urlparse(config.base_url).hostname}, "
        f"evidence={evidence_class(config.base_url)})",
        file=sys.stderr,
    )
    try:
        record = asyncio.run(
            investigate(
                workspace,
                target_id=args.target,
                mode="live",
                llm_config=config,
                wall_cap_s=args.wall_cap,
            )
        )
    except Exception as exc:  # report, never print the key
        message = str(exc).replace(config.api_key or "\0", "[redacted]")
        print(f"live_llm_check: investigation failed: {type(exc).__name__}: {message}", file=sys.stderr)
        return 3
    elapsed = time.monotonic() - started

    result = {
        "schema": SCHEMA,
        "evidence_class": evidence_class(config.base_url),
        "target": args.target,
        "provider": config.redacted(),
        "started_at": started_at.isoformat(),
        "elapsed_s": round(elapsed, 3),
        "live_path_accepted": record.get("mode_effective") == "live"
        and record.get("fallback_reason") is None,
        "summary": summarise(record),
        "record": record,
    }
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    if config.api_key and config.api_key in text:
        print("live_llm_check: refusing to write a result that contains the API key", file=sys.stderr)
        return 3

    out = Path(args.out)
    out.write_text(text + "\n")
    summary = result["summary"]
    print(
        f"live_llm_check: wrote {out} "
        f"(evidence={result['evidence_class']}, mode={summary['mode_effective']}, "
        f"fallback={summary['fallback_reason']}, verdict={summary['verdict']}, "
        f"elapsed={elapsed:.1f}s)"
    )
    return 0 if result["live_path_accepted"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", default="razorpay-vulnerable", help="bundled target id")
    parser.add_argument(
        "--out",
        default=f"live-llm-check-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json",
        help="where to write the result JSON",
    )
    parser.add_argument(
        "--wall-cap",
        type=float,
        default=None,
        help="investigation wall-clock cap in seconds (default: Duress's own cap)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args, dict(os.environ))


if __name__ == "__main__":
    sys.exit(main())
