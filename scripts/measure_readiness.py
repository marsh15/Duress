#!/usr/bin/env python3
"""Measure per-phase trial timings, above all the readiness phase, against its budget.

The one invalid public trial (`harness:target_unavailable`) is a probable readiness-budget
expiry on a Free-tier cold start (docs/investigations/INVALID_TRIAL.md). Nothing in the
retained record could say how close ordinary starts come to the 10 s budget. This script
produces that evidence; it never changes a budget or a verdict.

Two sources, one report:

    # Fresh trials on this machine (real supervisor, temporary runtime home):
    uv run python scripts/measure_readiness.py --trials 20 --out readiness-local.json

    # Trial rows already recorded by an investigation, local or exported from a host:
    curl -s https://HOST/api/investigations/INVESTIGATION_ID -o inv.json
    uv run python scripts/measure_readiness.py --from-record inv.json --out readiness-host.json

Exit codes:
    0  report produced
    2  no trial timings found, or invalid arguments (nothing measured)
    3  unexpected error

Reading the report: ``readiness.max_over_budget`` is the slowest observed readiness divided
by the readiness budget. A ratio near 1 (or any ``timed_out`` trial) means a slow start can
end a trial as INCONCLUSIVE `harness:target_unavailable`; the verdict rules are unaffected,
because an incomplete trial never counts as PASS or FAIL. Raise the budget only from a
sample taken on the slow host, and re-check `limits.py` (phases must sum to the watchdog).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "duress.readiness_measurement/1"
PHASES = ("prepare", "readiness", "execute", "quiesce", "drain", "kill_confirm", "evaluate")


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; deterministic and dependency free."""
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def describe(values: list[float]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "median": round(percentile(values, 0.5), 3),
        "p95": round(percentile(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def trial_rows(record: Any) -> list[dict[str, Any]]:
    """Trial rows from an investigation record, a list of rows, or a ScenarioResult dump."""
    if isinstance(record, list):
        return [row for row in record if isinstance(row, dict)]
    if not isinstance(record, dict):
        return []
    rows: list[dict[str, Any]] = []
    for scenario in record.get("scenarios") or []:
        rows.extend(scenario.get("trials") or [])
    if rows:
        return rows
    for index, observation in enumerate(record.get("observations") or []):
        verdicts = (record.get("verdicts") or [])
        verdict = verdicts[index] if index < len(verdicts) else {}
        rows.append({**observation, "status": verdict.get("status"), "reasons": verdict.get("reasons", [])})
    return rows


def build_report(rows: list[dict[str, Any]], *, budget_s: float, source: dict[str, Any]) -> dict[str, Any] | None:
    timed = [row for row in rows if isinstance(row.get("phase_timings"), dict) and row["phase_timings"]]
    if not timed:
        return None
    phases: dict[str, dict[str, float | int]] = {}
    for phase in PHASES:
        values = [float(row["phase_timings"][phase]) for row in timed if phase in row["phase_timings"]]
        if values:
            phases[phase] = describe(values)
    readiness = [float(row["phase_timings"]["readiness"]) for row in timed if "readiness" in row["phase_timings"]]
    unavailable = [
        row for row in timed
        if any("target_unavailable" in str(reason) for reason in row.get("reasons") or [])
    ]
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "trials_with_timings": len(timed),
        "trials_without_timings": len(rows) - len(timed),
        "phases": phases,
        "readiness_budget_s": budget_s,
        "target_unavailable_trials": len(unavailable),
        "statuses": _count(row.get("status") for row in timed),
    }
    if readiness:
        report["readiness"] = {
            **describe(readiness),
            "budget_s": budget_s,
            "max_over_budget": round(max(readiness) / budget_s, 3),
            "at_or_over_80_percent_of_budget": sum(1 for value in readiness if value >= 0.8 * budget_s),
            "timed_out": sum(1 for value in readiness if value >= budget_s),
        }
    return report


def _count(items) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[str(item)] = counts.get(str(item), 0) + 1
    return counts


async def measure_local(trials: int, target: str) -> list[dict[str, Any]]:
    """Run real S1 trials and keep each observation's phase timings and verdict."""
    from duress import limits as limits_module
    from duress.profiles import ProfileRegistry
    from duress.services import Workspace, run_target_scenario
    from duress.store import ensure_dirs

    home = Path(tempfile.mkdtemp(prefix="duress-readiness-"))
    ensure_dirs(home)
    workspace = Workspace(
        root=ROOT, home=home, registry=ProfileRegistry.discover(ROOT), limits=limits_module.DEFAULT_LIMITS
    )
    rows: list[dict[str, Any]] = []
    while len(rows) < trials:
        result = await run_target_scenario(workspace, target_id=target, template_id="S1", policy="explore")
        for observation, verdict in zip(result.observations, result.verdicts):
            rows.append({
                "trial_id": observation.trial_id,
                "status": verdict.status,
                "reasons": list(verdict.reasons),
                "phase_timings": dict(observation.phase_timings),
            })
    return rows[:trials]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--trials", type=int, help="run this many fresh S1 trials locally")
    source.add_argument("--from-record", type=Path, help="investigation/trial JSON to summarise (no execution)")
    parser.add_argument("--target", default="razorpay-fixed", help="target for --trials")
    parser.add_argument("--out", type=Path, help="write the JSON report here (default: stdout)")
    return parser


def run(args: argparse.Namespace) -> int:
    from duress import limits as limits_module

    budget_s = limits_module.PHASE_TIMEOUTS_S["readiness"]
    if args.trials is not None:
        if not 1 <= args.trials <= 200:
            print("measure_readiness: --trials must be between 1 and 200", file=sys.stderr)
            return 2
        rows = asyncio.run(measure_local(args.trials, args.target))
        source = {"kind": "local-trials", "target": args.target, "requested_trials": args.trials}
    else:
        try:
            record = json.loads(args.from_record.read_text())
        except (OSError, ValueError) as exc:
            print(f"measure_readiness: cannot read {args.from_record}: {type(exc).__name__}", file=sys.stderr)
            return 2
        rows = trial_rows(record)
        source = {"kind": "record", "file": args.from_record.name}
    report = build_report(rows, budget_s=budget_s, source=source)
    if report is None:
        print(
            "measure_readiness: no trial rows carry phase_timings; records written before "
            "timings were retained cannot be measured",
            file=sys.stderr,
        )
        return 2
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text)
        print(f"measure_readiness: wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    try:
        return run(build_parser().parse_args(argv))
    except Exception as exc:  # noqa: BLE001 - a script boundary: report, never traceback
        print(f"measure_readiness: unexpected error ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
