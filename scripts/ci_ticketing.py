#!/usr/bin/env python3
"""Run the independent ticketing acceptance controls against an installed Duress wheel.

The script intentionally invokes Duress through this Python interpreter so the
acceptance run cannot accidentally use the checkout's development environment.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


SCENARIO = "concurrent-duplicate"
CLI_LAUNCHER = "from duress.cli import app; app()"


class AcceptanceError(RuntimeError):
    """An acceptance control did not produce its expected result."""


def database_secret_parts(dsn: str) -> tuple[str, ...]:
    """Validate a local, dedicated PostgreSQL URI without exposing its secrets."""
    try:
        parsed = urlsplit(dsn)
        host = parsed.hostname or ""
        address_is_loopback = host.lower() == "localhost"
        if not address_is_loopback:
            address_is_loopback = ipaddress.ip_address(host).is_loopback
        database = unquote(parsed.path.removeprefix("/"))
        password = unquote(parsed.password or "")
        if (parsed.scheme not in {"postgres", "postgresql"} or not address_is_loopback
                or not parsed.username or not password or not database.endswith("_test")):
            raise ValueError
    except (ValueError, TypeError):
        raise AcceptanceError(
            "DURESS_TICKETING_DB must be a loopback PostgreSQL URI with a password "
            "and a database name ending in _test"
        ) from None
    encoded_password = quote(password, safe="")
    return dsn, password, encoded_password


def _write_json(path: Path, value: object, secrets: tuple[str, ...]) -> None:
    serialized = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if any(secret and secret in serialized for secret in secrets):
        raise AcceptanceError("refusing to write an acceptance artifact containing database credentials")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")


def _suppress_credential_artifacts(directory: Path, secrets: tuple[str, ...]) -> int:
    """Replace any accidentally credential-bearing output before CI uploads it."""
    if not directory.exists():
        return 0
    credential_bytes = tuple(secret.encode("utf-8") for secret in secrets if secret)
    suppressed = 0
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            contents = path.read_bytes()
        except OSError:
            continue
        if any(secret in contents for secret in credential_bytes):
            path.write_text('{"artifact_status":"suppressed_runtime_credentials"}\n', encoding="utf-8")
            suppressed += 1
    return suppressed


class Runner:
    def __init__(self, app: Path, artifact_dir: Path, timeout: int, secrets: tuple[str, ...]):
        self.app = app
        self.artifact_dir = artifact_dir
        self.timeout = timeout
        self.secrets = secrets
        self.records: list[dict[str, object]] = []

    def invoke(
        self,
        name: str,
        args: list[str],
        *,
        expected_exit: int,
        expected_outcomes: set[str],
        result_path: Path | None = None,
        outcome_exit_codes: dict[str, int] | None = None,
    ) -> dict:
        command = [sys.executable, "-c", CLI_LAUNCHER, *args]
        try:
            completed = subprocess.run(
                command,
                cwd=self.app,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.records.append({
                "name": name,
                "expected_exit_code": expected_exit,
                "actual_exit_code": None,
                "outcome": None,
                "passed": False,
                "reason": "timeout",
            })
            raise AcceptanceError(f"{name} timed out after {self.timeout} seconds") from None
        try:
            result = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            result = None

        outcome = result.get("outcome", result.get("verdict")) if isinstance(result, dict) else None
        expected_code = (outcome_exit_codes.get(outcome) if outcome_exit_codes and outcome else expected_exit)
        passed = (outcome in expected_outcomes and expected_code is not None
                  and completed.returncode == expected_code)
        record = {
            "name": name,
            "expected_exit_code": expected_code,
            "accepted_exit_codes": sorted(set(outcome_exit_codes.values())) if outcome_exit_codes else [expected_exit],
            "actual_exit_code": completed.returncode,
            "outcome": outcome,
            "passed": passed,
        }
        self.records.append(record)
        if result_path is not None and isinstance(result, dict):
            _write_json(result_path, result, self.secrets)
        if not isinstance(result, dict):
            raise AcceptanceError(f"{name} did not return JSON (exit {completed.returncode})")
        if not passed:
            expected_description = (str(expected_code) if expected_code is not None else
                                    ", ".join(map(str, sorted(set(outcome_exit_codes.values()))))
                                    if outcome_exit_codes else str(expected_exit))
            raise AcceptanceError(
                f"{name} expected exit {expected_description} and outcome "
                f"{', '.join(sorted(expected_outcomes))}; got exit {completed.returncode} "
                f"and outcome {outcome or 'missing'}"
            )
        print(f"{name}: expected={expected_code} actual={completed.returncode} outcome={outcome} PASS")
        return result


def _expect_check(runner: Runner, variant: str, expected: str, expected_exit: int) -> dict:
    check_dir = runner.artifact_dir / "checks"
    dna_dir = runner.artifact_dir / "dna" / variant
    report_json = check_dir / f"{variant}.json"
    report_xml = check_dir / f"{variant}.xml"
    result = runner.invoke(
        f"{variant}-check",
        [
            "check",
            "--config", str(runner.app / "duress" / f"{variant}.toml"),
            "--scenario", SCENARIO,
            "--output", str(report_json),
            "--junit", str(report_xml),
            "--artifacts", str(dna_dir),
            "--json",
        ],
        expected_exit=expected_exit,
        expected_outcomes={expected},
    )
    return result


def _dna_for_flawed_check(runner: Runner, result: dict) -> Path:
    dna_ids = result.get("dna")
    if not isinstance(dna_ids, list) or not dna_ids or not isinstance(dna_ids[0], str):
        raise AcceptanceError("flawed check did not produce Failure DNA")
    dna_path = runner.artifact_dir / "dna" / "flawed" / f"{dna_ids[0]}.json"
    try:
        document = json.loads(dna_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AcceptanceError("flawed check reported DNA that was not exported") from None
    if document.get("schema") != "external-dna@1" or document.get("dna_id") != dna_ids[0]:
        raise AcceptanceError("exported Failure DNA does not match the flawed check")
    return dna_path


def run_acceptance(app: Path, artifact_dir: Path, timeout: int, *, reduce: bool = False) -> dict:
    app = app.expanduser().resolve(strict=True)
    artifact_dir = artifact_dir.expanduser().resolve()
    duress_root = Path(__file__).resolve().parents[1]
    if not app.is_dir():
        raise AcceptanceError("ticketing application path must be a directory")
    if app.is_relative_to(duress_root):
        raise AcceptanceError("ticketing application must be a separate checkout outside the Duress source")
    if artifact_dir.is_relative_to(duress_root) or artifact_dir.is_relative_to(app):
        raise AcceptanceError("acceptance artifacts must be outside both source checkouts")
    for variant in ("flawed", "fixed"):
        if not (app / "duress" / f"{variant}.toml").is_file():
            raise AcceptanceError("ticketing configurations are missing; run setup_ticketing_example.py first")

    dsn = os.environ.get("DURESS_TICKETING_DB", "")
    secret_parts = database_secret_parts(dsn)
    runner = Runner(app, artifact_dir, timeout, secret_parts)
    try:
        flawed = _expect_check(runner, "flawed", "FAIL", 1)
        _expect_check(runner, "fixed", "PASS", 0)

        dna_path = _dna_for_flawed_check(runner, flawed)
        flawed_config = str(app / "duress" / "flawed.toml")
        fixed_config = str(app / "duress" / "fixed.toml")

        runner.invoke(
            "flawed-replay",
            ["replay", str(dna_path), "--config", flawed_config, "--json"],
            expected_exit=1,
            expected_outcomes={"REPRODUCED"},
            result_path=artifact_dir / "operations" / "replay.json",
        )
        runner.invoke(
            "fixed-verification",
            ["verify-fix", str(dna_path), "--config", flawed_config,
             "--candidate-config", fixed_config, "--json"],
            expected_exit=0,
            expected_outcomes={"VERIFIED"},
            result_path=artifact_dir / "operations" / "verify-fixed.json",
        )
        runner.invoke(
            "flawed-negative-control",
            ["verify-fix", str(dna_path), "--config", flawed_config,
             "--candidate-config", flawed_config, "--json"],
            expected_exit=1,
            expected_outcomes={"NOT_VERIFIED"},
            result_path=artifact_dir / "operations" / "verify-flawed.json",
        )
        if reduce:
            # The CLI result is recorded exactly as returned; this script makes
            # no minimality claim beyond Duress's bounded result.
            runner.invoke(
                "optional-reduction",
                ["reduce", str(dna_path), "--config", flawed_config, "--json"],
                expected_exit=0,
                expected_outcomes={"REDUCED", "NOT_REDUCED", "INCONCLUSIVE", "REJECTED"},
                outcome_exit_codes={"REDUCED": 0, "NOT_REDUCED": 0,
                                    "INCONCLUSIVE": 2, "REJECTED": 4},
                result_path=artifact_dir / "operations" / "reduce.json",
            )
        return {"schema": "duress-ticketing-ci@1", "checks": runner.records}
    finally:
        suppressed = _suppress_credential_artifacts(artifact_dir, secret_parts)
        summary = {"schema": "duress-ticketing-ci@1", "checks": runner.records,
                   "credential_bearing_artifacts_suppressed": suppressed}
        _write_json(artifact_dir / "summary.json", summary, secret_parts)
        if suppressed:
            raise AcceptanceError("runtime credentials were suppressed from generated artifacts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application", type=Path, help="independent ticketing application checkout")
    parser.add_argument("--artifacts-dir", type=Path, required=True,
                        help="output directory outside the Duress source checkout")
    parser.add_argument("--timeout", type=int, default=300,
                        help="per-command timeout in seconds (default: 300)")
    parser.add_argument("--reduce", action="store_true", help="also run the bounded reduction operation")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    try:
        run_acceptance(args.application, args.artifacts_dir, args.timeout, reduce=args.reduce)
    except AcceptanceError as exc:
        print(f"ticketing acceptance failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
