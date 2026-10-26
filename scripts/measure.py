#!/usr/bin/env python3
"""Run release suites and render evidence from saved, observed results.

Examples:
    uv run python scripts/measure.py --run fast
    uv run python scripts/measure.py --run m2
    uv run python scripts/measure.py --run pytest --pytest-arg=tests/unit/test_limits.py
    uv run python scripts/measure.py --render

Raw JUnit is always retained. By default it lives under ignored ``.duress``;
set ``DURESS_RECORD_MEASUREMENTS=1`` to additionally persist a unique JSON run
record and its JUnit files under ``docs/measurements``. Historical evidence is
read from ``docs/EVIDENCE.md`` and is never rewritten by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import contextlib
import importlib.util
import io
import json
import os
import platform
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MEASUREMENTS = ROOT / "docs" / "measurements"
HISTORICAL_EVIDENCE = ROOT / "docs" / "EVIDENCE.md"

SUITES: dict[str, list[str]] = {
    "fast": ["-q", "-m", "not slow"],
    "slow": ["-q", "-m", "slow"],
    "e2e": ["tests/e2e", "-q"],
    "m0": [
        "targets/razorpay_ref/tests",
        "tests/m0/test_e1_vulnerable_race.py",
        "tests/m0/test_e2_fixed_exactly_once.py",
        "tests/m0/test_e3_drain.py",
        "-q",
    ],
    "m1": [
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
        "-q",
    ],
    "m2": [
        "tests/unit/test_verify_decisions.py",
        "tests/integration/test_matrix_m2.py",
        "tests/integration/test_replay_preflight.py",
        "tests/integration/test_dna_journey.py",
        "tests/e2e/test_verify_truth_table.py",
        "tests/integration/test_api.py",
        "-q",
    ],
    "all": ["-q"],
    # A custom pytest suite is available with --pytest-arg (repeatable).
    "pytest": [],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def environment(uv: str) -> dict[str, Any]:
    try:
        uv_version = subprocess.run(
            [uv, "--version"], capture_output=True, text=True, check=False, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        uv_version = None
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        git_revision = revision.stdout.strip() if revision.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        git_revision = None
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True, timeout=10).stdout.strip()
    digest = hashlib.sha256()
    for directory in (ROOT / "src", ROOT / "targets", ROOT / "tests"):
        for source in sorted(directory.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix in {".py", ".html", ".css", ".js", ".json", ".toml"}:
                digest.update(source.relative_to(ROOT).as_posix().encode() + b"\0" + source.read_bytes())
    return {
        "worktree_dirty": bool(dirty),
        "source_tree_sha256": digest.hexdigest(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "uv": uv_version,
        "packages": {
            name: package_version(name)
            for name in ("pytest", "fastapi", "httpx", "pydantic", "uvicorn")
        },
        "git_revision": git_revision,
    }


def parse_junit(path: Path) -> dict[str, int | None]:
    """Return observed test counts from pytest's raw JUnit XML."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {"collected": None, "passed": None, "failed": None, "errors": None, "skipped": None}

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}

    def total(attribute: str) -> int:
        return sum(int(suite.attrib.get(attribute, "0")) for suite in suites)

    collected = total("tests")
    failed = total("failures")
    errors = total("errors")
    skipped = total("skipped")
    return {
        "collected": collected,
        "passed": max(collected - failed - errors - skipped, 0),
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
    }


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run pytest suites and print Markdown evidence, or render historical and recorded evidence."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--run",
        nargs="+",
        choices=sorted(SUITES),
        metavar="SUITE",
        help=(
            "execute one or more suites: fast, slow, e2e, m0, m1, m2, all, or pytest. "
            "pytest runs custom paths/options passed with --pytest-arg."
        ),
    )
    action.add_argument(
        "--render",
        action="store_true",
        help="print the existing historical evidence and any recorded release run JSON; run nothing.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="append one argument to every selected pytest command (repeatable; use --pytest-arg=-x for flags).",
    )
    return parser


def suite_args(suite: str, extra: list[str]) -> list[str]:
    if suite == "pytest" and not extra:
        raise ValueError("the 'pytest' suite needs at least one --pytest-arg path or option")
    if any(arg == "--junitxml" or arg.startswith("--junitxml=") for arg in extra):
        raise ValueError("--junitxml is managed by measure.py; omit it from --pytest-arg")
    return [*SUITES[suite], *extra]


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _record_markdown(record: dict[str, Any]) -> list[str]:
    lines = [
        f"### Run `{record.get('run_id', 'unknown')}`",
        "",
        f"Observed at: `{record.get('generated_at', 'unknown')}`. "
        f"Environment: Python `{record.get('environment', {}).get('python', 'unknown')}`, "
        f"SQLite `{record.get('environment', {}).get('sqlite', 'unknown')}`, "
        f"`{record.get('environment', {}).get('platform', 'unknown')}`.",
        "",
        markdown_table(
            ["Suite", "Command", "Exit", "Passed", "Failed", "Errors", "Skipped", "Seconds", "Raw JUnit"],
            [
                [
                    suite.get("name", "unknown"),
                    f"`{suite.get('command', '')}`",
                    suite.get("exit_code", "unknown"),
                    suite.get("counts", {}).get("passed", "unavailable"),
                    suite.get("counts", {}).get("failed", "unavailable"),
                    suite.get("counts", {}).get("errors", "unavailable"),
                    suite.get("counts", {}).get("skipped", "unavailable"),
                    suite.get("seconds", "unknown"),
                    f"`{suite.get('junit_xml', 'unavailable')}`",
                ]
                for suite in record.get("suites", [])
            ],
        ),
        "",
    ]
    return lines


def load_release_records(directory: Path = MEASUREMENTS) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("release-*.json")):
        try:
            document = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and isinstance(document.get("suites"), list):
            records.append(document)
    return records


def historical_document() -> str:
    """Load docs history, rebuilding it from source measurements if a shell
    redirect truncated the destination before ``--render`` could read it.
    """
    if HISTORICAL_EVIDENCE.exists():
        saved = HISTORICAL_EVIDENCE.read_text()
        if saved.strip():
            return saved

    # m0_evidence.py is the existing renderer for committed M0–M2 records.
    # Point its output at a temporary directory so this fallback cannot write
    # into the shared checkout, even when stdout is redirected to EVIDENCE.md.
    legacy_path = ROOT / "scripts" / "m0_evidence.py"
    spec = importlib.util.spec_from_file_location("duress_m0_evidence", legacy_path)
    if spec is None or spec.loader is None:
        return ""
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MEASUREMENTS = MEASUREMENTS
    with tempfile.TemporaryDirectory(prefix="duress-evidence-render-") as temporary:
        module.ROOT = Path(temporary)
        with contextlib.redirect_stdout(io.StringIO()):
            module.render()
        generated = Path(temporary) / "docs" / "EVIDENCE.md"
        return generated.read_text() if generated.exists() else ""


def render(historical: str | None = None, records: list[dict[str, Any]] | None = None) -> str:
    """Keep prior docs byte-for-byte, then append only observed release records."""
    if historical is None:
        historical = historical_document()
    records = load_release_records() if records is None else records
    out = historical.rstrip() + "\n\n## M6 — Release measurements\n\n"
    if not records:
        out += (
            "No M6 release runs have been recorded. Use `scripts/measure.py --run <suite>` "
            "with `DURESS_RECORD_MEASUREMENTS=1` to save a run. This section makes no "
            "claim about suites that have not run.\n"
        )
        return out
    out += "The rows below come from saved run records and their JUnit XML. Missing runs are not inferred.\n\n"
    for record in records:
        out += "\n".join(_record_markdown(record))
    return out


def write_json_exclusive(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique run IDs avoid replacing historical measurements; exclusive mode
    # turns even an unlikely collision into an error instead of data loss.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")


def execute(suites: list[str], extra: list[str]) -> tuple[dict[str, Any], int]:
    uv = shutil.which("uv") or "uv"
    recording = os.environ.get("DURESS_RECORD_MEASUREMENTS") == "1"
    identifier = run_id()
    artifact_root = (
        MEASUREMENTS / "release-runs" / identifier
        if recording
        else ROOT / ".duress" / "measurements" / "release-runs" / identifier
    )
    artifact_root.mkdir(parents=True, exist_ok=False)
    suite_records: list[dict[str, Any]] = []
    overall_ok = True

    for index, name in enumerate(suites, start=1):
        args = suite_args(name, extra)
        junit_path = artifact_root / f"{index:02d}-{name}" / "junit.xml"
        junit_path.parent.mkdir(parents=True, exist_ok=False)
        command = [uv, "run", "pytest", *args, f"--junitxml={junit_path}"]
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        before = time.monotonic()
        child_env = os.environ.copy()
        # The measure command owns release recording. Tests (especially the M0
        # harness) must not rewrite their historical per-test JSON artifacts.
        child_env.pop("DURESS_RECORD_MEASUREMENTS", None)
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                env=child_env,
            )
            output = result.stdout + result.stderr
            code = result.returncode
        except OSError as exc:
            output = f"{type(exc).__name__}: {exc}"
            code = 127
        seconds = round(time.monotonic() - before, 2)
        counts = parse_junit(junit_path)
        output_path = junit_path.relative_to(ROOT).as_posix()
        suite_records.append(
            {
                "name": name,
                "started_at": started,
                "command": shlex.join(command),
                "exit_code": code,
                "seconds": seconds,
                "counts": counts,
                "junit_xml": output_path,
                "failure_output": "\n".join(output.splitlines()[-80:]) if code else "",
            }
        )
        if code:
            overall_ok = False

    record: dict[str, Any] = {
        "schema_version": 1,
        "run_id": identifier,
        "generated_at": utc_now(),
        "recorded": recording,
        "environment": environment(uv),
        "suites": suite_records,
    }
    if recording:
        record_path = MEASUREMENTS / f"release-{identifier}.json"
        record["record_path"] = record_path.relative_to(ROOT).as_posix()
        write_json_exclusive(record_path, record)

    records = load_release_records()
    if not recording:
        records.append(record)
    print(render(records=records))
    if not recording:
        print(
            f"\nRun record JSON was not written because DURESS_RECORD_MEASUREMENTS is not 1. "
            f"Raw JUnit is under `{artifact_root.relative_to(ROOT).as_posix()}`."
        )
    else:
        print(f"\nSaved this run as `{record['record_path']}`.")
    return record, 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.render:
        print(render(), end="")
        return 0
    try:
        _record, status = execute(args.run, args.pytest_arg)
    except ValueError as exc:
        parser.error(str(exc))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
