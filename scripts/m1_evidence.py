#!/usr/bin/env python3
"""Run the M1 acceptance suite and record exactly what happened.

The JSON written here is the source for the M1 section of docs/EVIDENCE.md.
Nothing is hand-entered: counts and wall time come from the pytest process that
just ran.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEASUREMENTS = ROOT / "docs" / "measurements"

M1_TESTS = [
    "tests/unit/test_limits.py",
    "tests/unit/test_exec_semantics.py",
    "tests/unit/test_schedule_validation.py",
    "tests/unit/test_invariants.py",
    "tests/unit/test_evaluate_trial.py",
    "tests/unit/test_aggregate.py",
    "tests/unit/test_trial_phases.py",
    "tests/unit/test_profiles.py",
    "tests/unit/test_operations.py",
    "tests/integration/test_deadline.py",
    "tests/integration/test_isolation.py",
    "tests/integration/test_matrix_fast.py",
    "tests/integration/test_controls.py",
    "tests/integration/test_engine_vs_m0.py",
    "tests/integration/test_e8_two_workers.py",
    "tests/integration/test_declared_launch.py",
]


def run(command: list[str], timeout: int = 1800) -> tuple[int, str, float]:
    started = time.monotonic()
    process = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    return process.returncode, process.stdout + process.stderr, elapsed


def number(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="local", help="evidence label, e.g. local or docker")
    args = parser.parse_args()
    uv = shutil.which("uv") or "uv"
    code, output, elapsed = run([uv, "run", "pytest", "-q", "-m", "not slow"])
    passed = number(r"(\d+) passed", output)
    failed = number(r"(\d+) failed", output)
    deselected = number(r"(\d+) deselected", output)

    collect_code, collected_output, _ = run(
        [uv, "run", "pytest", "--collect-only", "-q", *M1_TESTS]
    )
    nodeids = [line for line in collected_output.splitlines() if "::" in line]

    document = {
        "milestone": "M1",
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "uvicorn": version("uvicorn"),
            "fastapi": version("fastapi"),
            "httpx": version("httpx"),
            "pydantic": version("pydantic"),
        },
        "fast_suite": {
            "command": 'uv run pytest -q -m "not slow"',
            "exit_code": code,
            "passed": passed,
            "failed": failed,
            "deselected": deselected,
            "seconds": round(elapsed, 2),
            "summary": next(
                (
                    line.strip()
                    for line in reversed(output.splitlines())
                    if "passed" in line or "failed" in line or "error" in line
                ),
                "",
            ),
            "failure_output": (
                "\n".join(output.splitlines()[-120:]) if code != 0 else ""
            ),
        },
        "acceptance_tests": {
            "files": M1_TESTS,
            "collected": len(nodeids),
            "collection_exit_code": collect_code,
        },
    }

    MEASUREMENTS.mkdir(parents=True, exist_ok=True)
    name = "m1.json" if args.label == "local" else f"m1-{args.label}.json"
    path = MEASUREMENTS / name
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(path)
    print(f"recorded {path.relative_to(ROOT)}")
    print(documented := json.dumps(document["fast_suite"], indent=2))
    if code != 0:
        print(document["fast_suite"]["failure_output"])
    return 0 if code == 0 and failed in (0, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())