#!/usr/bin/env python3
"""Record M2 acceptance evidence from executions, never from prose.

Runs the M2 acceptance files, builds one artifact from a real failure, runs the
portability script, and (with --docker) exercises the containerised API.
"""

from __future__ import annotations

import argparse
import json
import os
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

import httpx

ROOT = Path(__file__).resolve().parents[1]
MEASUREMENTS = ROOT / "docs" / "measurements"

M2_TESTS = [
    "tests/unit/test_verify_decisions.py",
    "tests/integration/test_matrix_m2.py",
    "tests/integration/test_replay_preflight.py",
    "tests/integration/test_dna_journey.py",
    "tests/e2e/test_verify_truth_table.py",
    "tests/integration/test_api.py",
]


def run(command: list[str], timeout: int = 2400, env: dict | None = None) -> tuple[int, str, float]:
    started = time.monotonic()
    process = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env
    )
    return process.returncode, process.stdout + process.stderr, time.monotonic() - started


def number(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def docker_smoke() -> dict:
    result: dict = {"attempted": True}
    code, output, seconds = run(["docker", "build", "-t", "duress:m2", "."])
    result |= {"build_exit": code, "build_seconds": round(seconds, 1)}
    if code != 0:
        result |= {"status": "build_failed", "tail": output[-800:]}
        return result
    run(["docker", "rm", "-f", "duress-m2-evidence"])
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            "duress-m2-evidence",
            "-p",
            "18001:8000",
            "duress:m2",
            "sh",
            "-c",
            "duress dna build razorpay-vulnerable --scenario S3 --copies 2 "
            "--fault rendezvous && exec duress serve --host 0.0.0.0 --port 8000",
        ]
    )
    try:
        healthy = False
        for _ in range(180):
            try:
                response = httpx.get("http://127.0.0.1:18001/api/health", timeout=2)
                if response.status_code == 200:
                    healthy = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        if not healthy:
            code, logs, _ = run(["docker", "logs", "duress-m2-evidence"])
            result |= {"status": "unhealthy", "tail": logs[-800:]}
            return result
        dna = run(
            [
                "docker",
                "exec",
                "duress-m2-evidence",
                "python",
                "-c",
                "import json,glob;print(json.load(open(glob.glob('/tmp/duress/dna/*.json')[0]))['dna_id'])",
            ]
        )[1].strip()
        with httpx.Client(base_url="http://127.0.0.1:18001", timeout=120) as client:
            replay = client.post(f"/api/dna/{dna}/replay", json={}).json()
            verify = client.post(f"/api/dna/{dna}/verify-fix", json={}).json()
        result |= {
            "status": "pass",
            "dna_id": dna,
            "replay_outcome": replay["outcome"]["outcome"],
            "verify_outcome": verify["outcome"],
            "host_bind": "0.0.0.0",
        }
    finally:
        run(["docker", "rm", "-f", "duress-m2-evidence"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", action="store_true", help="also exercise the container API")
    args = parser.parse_args(argv)
    uv = shutil.which("uv") or "uv"

    code, output, seconds = run([uv, "run", "pytest", "-q", *M2_TESTS])
    passed = number(r"(\d+) passed", output)
    failed = number(r"(\d+) failed", output)

    with time_limited_home() as home:
        build_code, build_output, _ = run(
            [
                uv,
                "run",
                "duress",
                "dna",
                "build",
                "razorpay-vulnerable",
                "--scenario",
                "S3",
                "--copies",
                "2",
                "--fault",
                "rendezvous",
                "--json",
            ],
            env={**os.environ, "DURESS_HOME": str(home)},
        )
        try:
            document = json.loads(build_output)
        except ValueError:
            document = {}

    port_code, port_output, _ = run(["bash", "scripts/portability.sh"])

    document_out = {
        "milestone": "M2",
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
        "acceptance": {
            "files": M2_TESTS,
            "command": "uv run pytest -q " + " ".join(M2_TESTS),
            "exit_code": code,
            "passed": passed,
            "failed": failed,
            "seconds": round(seconds, 2),
            "summary": next(
                (
                    line.strip()
                    for line in reversed(output.splitlines())
                    if "passed" in line or "failed" in line
                ),
                "",
            ),
            "failure_output": "\n".join(output.splitlines()[-80:]) if code else "",
        },
        "artifact": {
            "build_exit": build_code,
            "dna_id": document.get("dna_id"),
            "signature": document.get("reproducer", {}).get("failure_signature"),
            "control_level": document.get("reproducer", {}).get("control_level"),
            "release_condition": document.get("reproducer", {}).get("release_condition"),
            "p0": document.get("characterization", {}).get("p0"),
            "valid": document.get("characterization", {}).get("valid"),
            "invariant_members": len(
                document.get("reproducer", {}).get("invariant_pack", {}).get("members", [])
            ),
        },
        "portability": {
            "exit_code": port_code,
            "passed": port_code == 0,
            "tail": port_output[-1200:],
        },
        "docker": docker_smoke() if args.docker else {"attempted": False},
    }

    MEASUREMENTS.mkdir(parents=True, exist_ok=True)
    path = MEASUREMENTS / "m2.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document_out, indent=2) + "\n")
    temporary.replace(path)
    print(f"recorded {path.relative_to(ROOT)}")
    print(json.dumps({k: v for k, v in document_out.items() if k != "portability"}, indent=2)[:1200])
    ok = (
        code == 0
        and not failed
        and port_code == 0
        and build_code == 0
        and (not args.docker or document_out["docker"].get("status") == "pass")
    )
    return 0 if ok else 1


class time_limited_home:
    def __enter__(self) -> Path:
        import tempfile

        self._dir = tempfile.TemporaryDirectory(prefix="duress-m2-")
        return Path(self._dir.name) / ".duress"

    def __exit__(self, *_exc) -> None:
        self._dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())