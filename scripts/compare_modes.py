#!/usr/bin/env python3
"""Live-versus-exhaustive comparison on the bundled vulnerable target.

Runs each requested mode N times on one target and records, per run:

* scenario coverage: which library templates were selected and run, and what
  fraction of the closed library that is;
* verdict, mode actually used, fallback reason, usable hypotheses;
* wall-clock time for the whole investigation;
* the number of model calls made (live mode only).

Exhaustive mode never calls a model. Live mode needs a provider.

Usage:
    # Dry run against the local mock provider (no key, no hosted model).
    uv run python scripts/compare_modes.py --mock --runs 1

    # Real comparison, with DURESS_LLM_BASE_URL and DURESS_LLM_MODEL set.
    uv run python scripts/compare_modes.py --runs 5 --out compare.json

Exit codes:
    0  every run completed
    1  at least one run raised; the error is recorded in the JSON
    2  missing configuration (no --mock and no DURESS_LLM_* env), or bad arguments

With --mock the result is labelled ``mock-provider``. A mock result shows the
pipeline works end to end. It says nothing about any hosted model's behaviour.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from live_llm_check import evidence_class, missing_env_message  # noqa: E402

SCHEMA = "duress.compare_modes/1"
MODES = ("live", "exhaustive")
MOCK_MODEL = "mock-model"


def template_of(scenario_label: str) -> str:
    """``S3[copies=3,fault=rendezvous]`` -> ``S3``."""
    return scenario_label.split("[", 1)[0]


def scenario_coverage(record: dict[str, Any], library_ids: set[str]) -> dict[str, Any]:
    run_templates = sorted({template_of(label) for label in record.get("selected_scenarios", [])})
    covered = [template for template in run_templates if template in library_ids]
    return {
        "templates": run_templates,
        "scenario_count": len(record.get("selected_scenarios", [])),
        "library_size": len(library_ids),
        "fraction_of_library": round(len(covered) / len(library_ids), 4) if library_ids else 0.0,
    }


def one_run(
    *,
    mode: str,
    target: str,
    config: Any,
    wall_cap_s: float | None,
    library_ids: set[str],
) -> dict[str, Any]:
    from duress import limits as limits_module
    from duress.profiles import ProfileRegistry
    from duress.services import Workspace, investigate
    from duress.store import ensure_dirs

    home = Path(tempfile.mkdtemp(prefix=f"duress-compare-{mode}-"))
    ensure_dirs(home)
    workspace = Workspace(
        root=ROOT,
        home=home,
        registry=ProfileRegistry.discover(ROOT),
        limits=limits_module.DEFAULT_LIMITS,
    )
    started = time.monotonic()
    record = asyncio.run(
        investigate(
            workspace,
            target_id=target,
            mode=mode,
            llm_config=config if mode == "live" else None,
            wall_cap_s=wall_cap_s,
        )
    )
    elapsed = time.monotonic() - started
    model_calls = (record.get("model") or {}).get("calls") or []
    return {
        "mode_requested": mode,
        "mode_effective": record.get("mode_effective"),
        "fallback_reason": record.get("fallback_reason"),
        "verdict": record.get("verdict"),
        "usable_hypotheses": record.get("usable_hypotheses"),
        "trials_used": record.get("trials_used"),
        "elapsed_s": round(elapsed, 3),
        "model_calls": len(model_calls),
        "coverage": scenario_coverage(record, library_ids),
        "error": None,
    }


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [item for item in runs if item["error"] is None]
    if not ok:
        return {"runs": len(runs), "completed": 0}
    times = [item["elapsed_s"] for item in ok]
    fallbacks: dict[str, int] = {}
    verdicts: dict[str, int] = {}
    for item in ok:
        if item["fallback_reason"]:
            fallbacks[item["fallback_reason"]] = fallbacks.get(item["fallback_reason"], 0) + 1
        verdicts[str(item["verdict"])] = verdicts.get(str(item["verdict"]), 0) + 1
    return {
        "runs": len(runs),
        "completed": len(ok),
        "mode_effective_counts": _count([item["mode_effective"] for item in ok]),
        "fallback_reasons": fallbacks,
        "verdicts": verdicts,
        "elapsed_s_mean": round(statistics.fmean(times), 3),
        "elapsed_s_median": round(statistics.median(times), 3),
        "coverage_fraction_mean": round(
            statistics.fmean(item["coverage"]["fraction_of_library"] for item in ok), 4
        ),
        "scenario_count_mean": round(
            statistics.fmean(item["coverage"]["scenario_count"] for item in ok), 3
        ),
        "model_calls_mean": round(statistics.fmean(item["model_calls"] for item in ok), 3),
    }


def _count(values: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def print_table(summary: dict[str, dict[str, Any]], evidence: str) -> None:
    print(f"compare_modes: evidence={evidence}")
    header = f"{'mode':<12}{'runs':>6}{'mean_s':>10}{'coverage':>10}{'verdicts':>30}{'fallbacks':>30}"
    print(header)
    for mode, stats in summary.items():
        print(
            f"{mode:<12}{stats.get('completed', 0):>6}"
            f"{stats.get('elapsed_s_mean', 0):>10}"
            f"{stats.get('coverage_fraction_mean', 0):>10}"
            f"{json.dumps(stats.get('verdicts', {})):>30}"
            f"{json.dumps(stats.get('fallback_reasons', {})):>30}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=3, help="repetitions per mode (default 3)")
    parser.add_argument("--target", default="razorpay-vulnerable", help="bundled target id")
    parser.add_argument(
        "--modes", default=",".join(MODES), help="comma list from: live,exhaustive"
    )
    parser.add_argument(
        "--mock", action="store_true", help="use the local mock provider (dry run, no key)"
    )
    parser.add_argument(
        "--wall-cap", type=float, default=None,
        help="per-investigation wall cap in seconds (default: Duress's own cap)",
    )
    parser.add_argument(
        "--out",
        default=f"compare-modes-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json",
        help="where to write the result JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    if args.runs < 1 or not modes or any(mode not in MODES for mode in modes):
        print("compare_modes: --runs must be >= 1 and --modes a comma list from live,exhaustive",
              file=sys.stderr)
        return 2

    from duress.investigator import SCENARIO_LIBRARY
    from duress.llm import LlmConfig

    library_ids = {entry["id"] for entry in SCENARIO_LIBRARY}
    env = dict(os.environ)
    mock_server = None
    if args.mock:
        from mock_openai_server import MockOpenAIServer, Reply, proposal_text, vulnerable_proposal

        mock_server = MockOpenAIServer([Reply.ok(proposal_text(vulnerable_proposal()))])
        mock_server.__enter__()
        config = LlmConfig(base_url=mock_server.base_url, model=MOCK_MODEL, api_key=None)
        evidence = "mock-provider"
    else:
        message = missing_env_message(env) if "live" in modes else None
        if message:
            print(message, file=sys.stderr)
            return 2
        config = LlmConfig.from_env(env)
        evidence = evidence_class(config.base_url) if config else "none"

    runs: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    errors = 0
    try:
        for mode in modes:
            for index in range(args.runs):
                print(f"compare_modes: {mode} run {index + 1}/{args.runs}", file=sys.stderr)
                try:
                    result = one_run(
                        mode=mode,
                        target=args.target,
                        config=config,
                        wall_cap_s=args.wall_cap,
                        library_ids=library_ids,
                    )
                except Exception as exc:  # recorded; the other runs still happen
                    errors += 1
                    message = str(exc)
                    if config is not None and config.api_key:
                        message = message.replace(config.api_key, "[redacted]")
                    result = {"mode_requested": mode, "error": f"{type(exc).__name__}: {message}"}
                runs[mode].append(result)
    finally:
        if mock_server is not None:
            mock_server.__exit__(None, None, None)

    summary = {mode: aggregate(items) for mode, items in runs.items()}
    payload = {
        "schema": SCHEMA,
        "evidence_class": evidence,
        "target": args.target,
        "modes": modes,
        "runs_per_mode": args.runs,
        "wall_cap_s": args.wall_cap,
        "provider": config.redacted() if config is not None else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "runs": runs,
    }
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if config is not None and config.api_key and config.api_key in text:
        print("compare_modes: refusing to write a result that contains the API key", file=sys.stderr)
        return 1
    Path(args.out).write_text(text + "\n")
    print_table(summary, evidence)
    print(f"compare_modes: wrote {args.out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
