"""Independent M0 harness: real target processes, real HTTP, real SQLite copies.

No Duress engine code is imported here. Each trial gets its own process group,
database, port, webhook secret and provider namespace. State is read only from a
copy taken after the process group is confirmed gone.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fastapi
import httpx
import uvicorn

from .stub import StubServer
from .util import free_port, wait_for_http

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGETS_DIR = REPO_ROOT / "targets"
SEED_PATH = TARGETS_DIR / "razorpay_ref" / "seed.json"
MEASUREMENTS_DIR = REPO_ROOT / "docs" / "measurements"

TABLES = ("orders", "payments", "fulfilments")


# --------------------------------------------------------------------------- #
# Payloads and fixtures
# --------------------------------------------------------------------------- #
def captured_fixtures() -> dict[str, dict]:
    return {
        "pay_A1": {
            "id": "pay_A1",
            "order_id": "order_A",
            "amount": 5000,
            "currency": "INR",
            "status": "captured",
        },
        "pay_A2": {
            "id": "pay_A2",
            "order_id": "order_A",
            "amount": 5000,
            "currency": "INR",
            "status": "captured",
        },
    }


def payment_captured_payload(payment_id: str = "pay_A1", order_id: str = "order_A") -> dict:
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 5000,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class Attempt:
    delivery_id: str
    outcome: str  # response | timeout | connect_error
    status_code: int | None = None
    body: dict | None = None
    elapsed_s: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "delivery_id": self.delivery_id,
            "outcome": self.outcome,
            "status_code": self.status_code,
            "body": self.body,
            "elapsed_s": round(self.elapsed_s, 4),
            "error": self.error,
        }


@dataclass
class Trial:
    trial_id: str
    variant: str
    copies: int
    payment_id: str
    valid: bool
    invalid_reasons: list[str]
    responses: list[Attempt]
    exit_code: int | None
    forced: bool
    group_gone: bool
    instance_ok: bool
    state_readable: bool
    released_by: str | None
    arrivals: int
    stub_calls: int
    stale_calls: int
    phase_timings: dict[str, float]
    total_s: float
    fulfilments: list[dict] = field(default_factory=list)
    payment: dict | None = None
    order: dict | None = None
    baseline: dict[str, list[dict]] = field(default_factory=dict)
    workdir: str = ""

    @property
    def fulfilment_count(self) -> int:
        return len(self.fulfilments)

    @property
    def linked_count(self) -> int:
        return sum(1 for row in self.fulfilments if row["payment_id"] == self.payment_id)

    def to_dict(self) -> dict:
        return {
            "trial_id": self.trial_id,
            "variant": self.variant,
            "copies": self.copies,
            "payment_id": self.payment_id,
            "valid": self.valid,
            "invalid_reasons": self.invalid_reasons,
            "responses": [r.to_dict() for r in self.responses],
            "exit_code": self.exit_code,
            "forced": self.forced,
            "group_gone": self.group_gone,
            "instance_ok": self.instance_ok,
            "state_readable": self.state_readable,
            "released_by": self.released_by,
            "arrivals": self.arrivals,
            "stub_calls": self.stub_calls,
            "stale_calls": self.stale_calls,
            "phase_timings": {k: round(v, 4) for k, v in self.phase_timings.items()},
            "total_s": round(self.total_s, 4),
            "fulfilment_count": self.fulfilment_count,
            "linked_count": self.linked_count,
            "payment": self.payment,
            "order": self.order,
        }


def base_env(extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(TARGETS_DIR) + (os.pathsep + existing if existing else "")
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra)
    return env


def _read_rows(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(sql, params)
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def snapshot_tables(db_path: Path) -> dict[str, list[dict]]:
    return {
        table: _read_rows(db_path, f"SELECT * FROM {table} ORDER BY rowid")
        for table in TABLES
    }


class TrialRunner:
    def __init__(
        self,
        *,
        variant: str,
        stub: StubServer,
        workdir_root: Path,
        copies: int = 1,
        latency_ms: int = 0,
        parties: int | None = None,
        rendezvous_timeout_ms: int = 2000,
        fixtures: dict[str, dict] | None = None,
        attempt_timeout_s: float = 15.0,
        sigterm_after_s: float | None = None,
        payment_id: str = "pay_A1",
        allow_timeouts: bool = False,
        keep_evidence: bool = False,
    ) -> None:
        self.variant = variant
        self.stub = stub
        self.workdir_root = Path(workdir_root)
        self.copies = copies
        self.latency_ms = latency_ms
        self.parties = parties
        self.rendezvous_timeout_ms = rendezvous_timeout_ms
        self.fixtures = fixtures or captured_fixtures()
        self.attempt_timeout_s = attempt_timeout_s
        self.sigterm_after_s = sigterm_after_s
        self.payment_id = payment_id
        self.allow_timeouts = allow_timeouts
        self.keep_evidence = keep_evidence

        self.trial_id = "t" + uuid.uuid4().hex[:12]
        self.workdir = Path(tempfile.mkdtemp(prefix=f"duress-{self.trial_id}-", dir=self.workdir_root))
        self.db_path = self.workdir / "app.db"
        self.port = free_port()
        self.secret = secrets.token_hex(16)
        self.pgid: int | None = None
        self.proc: subprocess.Popen | None = None
        self.sigterm_sent = False
        self._log = None
        self.phase_timings: dict[str, float] = {}

    # -- lifecycle -----------------------------------------------------------
    def run(self) -> Trial:
        started = time.perf_counter()
        self._prepare()
        self._start()
        self._dispatch()
        self._drain()
        group_gone = self._confirm_group_gone()
        state = self._snapshot()

        stats = self.stub.stats(self.trial_id)
        self.stub.retire(self.trial_id)

        reasons: list[str] = []
        if not self.instance_ok:
            reasons.append("instance_mismatch")
        if any(response is None for response in self.responses):
            reasons.append("attempt_missing")
        elif not self.allow_timeouts and any(r.outcome != "response" for r in self.responses):
            reasons.append("attempt_not_answered")
        if self.exit_code != 0:
            reasons.append(f"exit_code_{self.exit_code}")
        if self.forced:
            reasons.append("forced_termination")
        if not group_gone:
            reasons.append("group_not_gone")
        if not state["readable"]:
            reasons.append("state_unreadable")
        if stats["stale_calls"]:
            reasons.append("stale_call")

        total_s = time.perf_counter() - started
        trial = Trial(
            trial_id=self.trial_id,
            variant=self.variant,
            copies=self.copies,
            payment_id=self.payment_id,
            valid=not reasons,
            invalid_reasons=reasons,
            responses=list(self.responses),
            exit_code=self.exit_code,
            forced=self.forced,
            group_gone=group_gone,
            instance_ok=self.instance_ok,
            state_readable=state["readable"],
            released_by=stats["released_by"],
            arrivals=stats["arrivals"],
            stub_calls=stats["calls"],
            stale_calls=stats["stale_calls"],
            phase_timings=dict(self.phase_timings),
            total_s=total_s,
            fulfilments=state["fulfilments"],
            payment=state["payment"],
            order=state["order"],
            baseline=self.baseline,
            workdir=str(self.workdir),
        )

        if trial.valid and not self.keep_evidence:
            shutil.rmtree(self.workdir, ignore_errors=True)
        return trial

    def _prepare(self) -> None:
        start = time.perf_counter()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "razorpay_ref",
                "init-db",
                "--db",
                str(self.db_path),
                "--seed",
                str(SEED_PATH),
                "--variant",
                self.variant,
            ],
            cwd=str(REPO_ROOT),
            env=base_env({}),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"init-db failed: {result.stderr.decode()}")
        self.baseline = snapshot_tables(self.db_path)
        self.stub.create_namespace(
            self.trial_id,
            self.fixtures,
            latency_ms=self.latency_ms,
            parties=self.parties,
            rendezvous_timeout_ms=self.rendezvous_timeout_ms,
        )
        self.phase_timings["prepare"] = time.perf_counter() - start

    def _start(self) -> None:
        start = time.perf_counter()
        env = base_env(
            {
                "DATABASE_PATH": str(self.db_path),
                "PROVIDER_BASE_URL": f"{self.stub.base_url}/t/{self.trial_id}",
                "WEBHOOK_SECRET": self.secret,
                "DURESS_INSTANCE": self.trial_id,
            }
        )
        self._log = open(self.workdir / "target.log", "wb")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "razorpay_ref.serve",
                "--variant",
                self.variant,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            start_new_session=True,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self.pgid = os.getpgid(self.proc.pid)
        (self.workdir / "run.pid").write_text(
            json.dumps(
                {
                    "pgid": self.pgid,
                    "instance": self.trial_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
        try:
            health = wait_for_http(f"http://127.0.0.1:{self.port}/healthz", timeout_s=10)
            self.instance_ok = health.get("instance") == self.trial_id
        except TimeoutError:
            self.instance_ok = False
        self.phase_timings["readiness"] = time.perf_counter() - start

    def _dispatch(self) -> None:
        start = time.perf_counter()
        payload = payment_captured_payload(self.payment_id)
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Razorpay-Event-Id": "evt_" + self.trial_id,
            # Independent HMAC, deliberately not imported from the target.
            "X-Razorpay-Signature": hmac.new(
                self.secret.encode(), body, hashlib.sha256
            ).hexdigest(),
        }
        url = f"http://127.0.0.1:{self.port}/webhooks/razorpay"
        barrier = threading.Barrier(self.copies)
        results: list[Attempt | None] = [None] * self.copies

        def worker(index: int) -> None:
            barrier.wait()
            attempt_started = time.perf_counter()
            try:
                with httpx.Client(timeout=httpx.Timeout(self.attempt_timeout_s)) as client:
                    response = client.post(url, content=body, headers=headers)
                try:
                    parsed = response.json()
                except ValueError:
                    parsed = None
                results[index] = Attempt(
                    delivery_id=f"d{index + 1}",
                    outcome="response",
                    status_code=response.status_code,
                    body=parsed,
                    elapsed_s=time.perf_counter() - attempt_started,
                )
            except httpx.TimeoutException as exc:
                results[index] = Attempt(
                    delivery_id=f"d{index + 1}",
                    outcome="timeout",
                    elapsed_s=time.perf_counter() - attempt_started,
                    error=str(exc),
                )
            except httpx.HTTPError as exc:
                results[index] = Attempt(
                    delivery_id=f"d{index + 1}",
                    outcome="connect_error",
                    elapsed_s=time.perf_counter() - attempt_started,
                    error=str(exc),
                )

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(self.copies)]
        for thread in threads:
            thread.start()
        if self.sigterm_after_s is not None:
            time.sleep(self.sigterm_after_s)
            self._signal_group(signal.SIGTERM)
            self.sigterm_sent = True
        for thread in threads:
            thread.join()
        self.responses = results
        self.phase_timings["execute"] = time.perf_counter() - start

    def _drain(self) -> None:
        start = time.perf_counter()
        if not self.sigterm_sent:
            self._signal_group(signal.SIGTERM)
            self.sigterm_sent = True
        try:
            self.exit_code = self.proc.wait(timeout=10)
            self.forced = False
        except subprocess.TimeoutExpired:
            self._signal_group(signal.SIGKILL)
            self.exit_code = self.proc.wait(timeout=5)
            self.forced = True
        self.phase_timings["drain"] = time.perf_counter() - start

    def _confirm_group_gone(self, timeout_s: float = 5.0) -> bool:
        start = time.perf_counter()
        deadline = start + timeout_s
        gone = False
        while time.monotonic() < deadline:
            try:
                os.killpg(self.pgid, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.02)
        self.phase_timings["kill_confirm"] = time.perf_counter() - start
        return gone

    def _snapshot(self) -> dict:
        start = time.perf_counter()
        result: dict[str, Any] = {
            "readable": False,
            "fulfilments": [],
            "payment": None,
            "order": None,
        }
        snapshot_dir = self.workdir / "snapshot"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self.db_path) + suffix)
            if source.exists():
                shutil.copy2(source, snapshot_dir / f"app.db{suffix}")
        snapshot_db = snapshot_dir / "app.db"
        try:
            result["fulfilments"] = _read_rows(
                snapshot_db, "SELECT * FROM fulfilments WHERE order_id = ? ORDER BY id", ("order_A",)
            )
            payments = _read_rows(snapshot_db, "SELECT * FROM payments WHERE id = ?", (self.payment_id,))
            orders = _read_rows(snapshot_db, "SELECT * FROM orders WHERE id = ?", ("order_A",))
            result["payment"] = payments[0] if payments else None
            result["order"] = orders[0] if orders else None
            result["readable"] = True
        except sqlite3.Error as exc:
            result["error"] = str(exc)
        self.phase_timings["evaluate"] = time.perf_counter() - start
        return result

    def _signal_group(self, sig: int) -> None:
        if self.pgid is None:
            return
        try:
            os.killpg(self.pgid, sig)
        except ProcessLookupError:
            pass


def run_trial(**kwargs) -> Trial:
    stub = kwargs.pop("stub")
    workdir_root = kwargs.pop("workdir_root")
    return TrialRunner(stub=stub, workdir_root=workdir_root, **kwargs).run()


def valid_trials(trials: list[Trial]) -> list[Trial]:
    return [trial for trial in trials if trial.valid]


def run_until_valid(make_trial, required: int, max_attempts: int) -> list[Trial]:
    trials: list[Trial] = []
    while len(valid_trials(trials)) < required and len(trials) < max_attempts:
        trials.append(make_trial())
    return trials


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "uvicorn": uvicorn.__version__,
        "fastapi": fastapi.__version__,
        "httpx": httpx.__version__,
    }


def write_measurement(name: str, payload: dict) -> Path:
    MEASUREMENTS_DIR.mkdir(parents=True, exist_ok=True)
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": environment(),
        **payload,
    }
    path = MEASUREMENTS_DIR / f"{name}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(path)
    return path
