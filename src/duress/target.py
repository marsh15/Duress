"""Phased trial supervisor (plan C.2.2).

Every trial gets a fresh process group, database, port, secret, provider
namespace and instance id. State is read only from a copy taken after the group
is confirmed gone. The supervisor never reuses a process, and it refuses any
bind address other than loopback.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import limits as limits_module
from .models import (
    BuiltScenario,
    RendezvousFault,
    StubObservation,
    TableSnapshot,
    TrialObservation,
)
from .profiles import TargetProfile, load_seed
from .provider_stub import RendezvousRule, StubServer, free_port
from .runner import ExecutionResult, run_schedule
from .store import (
    Pidfile,
    duress_home,
    group_exists,
    group_members,
    process_start_time,
    remove_pidfile,
    write_pidfile,
)

TABLES = ("orders", "payments", "fulfilments")
PACK_COLUMNS = {
    "orders": {"id", "amount", "currency", "status"},
    "payments": {"id", "order_id", "amount", "currency", "status"},
    "fulfilments": {"id", "order_id", "payment_id", "amount", "created_at"},
}
TIMEOUT = object()


class HarnessError(RuntimeError):
    pass


@dataclass
class TrialConfig:
    profile: TargetProfile
    scenario: BuiltScenario
    stub: StubServer
    workdir_root: Path
    limits: limits_module.Limits = field(default_factory=lambda: limits_module.DEFAULT_LIMITS)
    host: str = "127.0.0.1"
    instance_override: str | None = None
    home: Path | None = None
    drain_verified: bool = True
    keep_evidence: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _base_env(profile: TargetProfile) -> dict[str, str]:
    env = os.environ.copy()
    targets = str(profile.cwd / "targets")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = targets + (os.pathsep + existing if existing else "")
    # Profile commands say ``python``; resolve it to the interpreter running
    # Duress even when the CLI was launched by absolute path without an
    # activated virtualenv.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def read_tables(db_path: Path) -> dict[str, list[dict[str, Any]]]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {table: _rows(conn, table) for table in TABLES}
    finally:
        conn.close()


def probe_schema(db_path: Path) -> tuple[bool, str | None]:
    conn = sqlite3.connect(str(db_path))
    try:
        for table, required in PACK_COLUMNS.items():
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in cursor.fetchall()}
            if not columns:
                return False, f"missing table {table}"
            missing = required - columns
            if missing:
                return False, f"{table} missing columns {sorted(missing)}"
        return True, None
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        conn.close()


def _stub_configuration(
    scenario: BuiltScenario,
) -> dict[str, Any]:
    latency_by_payment: dict[str, int] = {}
    rendezvous_by_payment: dict[str, RendezvousRule] = {}
    default_latency = 0
    default_rendezvous: RendezvousRule | None = None

    def payment_ids(payments: list[str] | None) -> list[str] | None:
        if payments is None:
            return None
        return [scenario.roles[role] for role in payments]

    for fault in scenario.schedule.faults:
        ids = payment_ids(fault.payments)
        if fault.kind == "provider_latency":
            if ids is None:
                default_latency = fault.ms
            else:
                for payment_id in ids:
                    latency_by_payment[payment_id] = fault.ms
        elif isinstance(fault, RendezvousFault):
            rule = RendezvousRule(parties=fault.parties, timeout_ms=fault.timeout_ms)
            if ids is None:
                default_rendezvous = rule
            else:
                for payment_id in ids:
                    rendezvous_by_payment[payment_id] = rule

    return {
        "latency_by_payment": latency_by_payment,
        "default_latency_ms": default_latency,
        "rendezvous_by_payment": rendezvous_by_payment,
        "default_rendezvous": default_rendezvous,
    }


class TrialSupervisor:
    def __init__(self, config: TrialConfig) -> None:
        if config.host != "127.0.0.1":
            raise ValueError(
                f"managed targets bind 127.0.0.1 only (plan P4f); refused {config.host!r}"
            )
        self.config = config
        self.limits = config.limits
        self.trial_id = "t" + uuid.uuid4().hex[:12]
        self.home = config.home or duress_home(config.profile.cwd)
        self.workdir = Path(
            tempfile.mkdtemp(prefix=f"duress-{self.trial_id}-", dir=str(config.workdir_root))
        )
        self.db_path = self.workdir / "app.db"
        self.port = free_port()
        self.secret = secrets.token_hex(16)
        self.expected_instance = self.trial_id
        self.spawned_instance = config.instance_override or self.trial_id
        self.proc: asyncio.subprocess.Process | None = None
        self.pgid: int | None = None
        self.log_handle: Any = None
        self.pidfile_written = False
        self.execution: ExecutionResult = ExecutionResult()
        self.phase_timings: dict[str, float] = {}
        self.started_monotonic = time.monotonic()
        self.stale_baseline = config.stub.registry.stale_total()
        self.observation = TrialObservation(
            trial_id=self.trial_id,
            target_id=config.profile.target_id,
            scenario_id=config.scenario.scenario_id,
            started_at=_now(),
            workdir=str(self.workdir),
        )

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    async def run(self) -> TrialObservation:
        try:
            try:
                await asyncio.wait_for(self._phases(), timeout=self.limits.watchdog_s)
            except asyncio.TimeoutError:
                self.observation.watchdog_fired = True
                self.observation.harness_errors.append("watchdog")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a harness bug, never a target verdict
                self.observation.harness_errors.append(
                    f"harness_error:{type(exc).__name__}"
                )
        finally:
            try:
                await asyncio.shield(self._settle())
            except asyncio.CancelledError:
                self._force_kill_sync()
                self.observation.duration_s = time.monotonic() - self.started_monotonic
                raise

        self.observation.exit_code = self.proc.returncode if self.proc else None
        self.observation.phase_timings = {
            key: round(value, 6) for key, value in self.phase_timings.items()
        }
        self.observation.duration_s = time.monotonic() - self.started_monotonic
        self.observation.stub = StubObservation(**self._stub_stats())
        return self.observation

    # ------------------------------------------------------------------ #
    # Phases
    # ------------------------------------------------------------------ #
    async def _phases(self) -> None:
        prepared = await self._timed("prepare", self._prepare())
        if prepared is TIMEOUT:
            self.observation.harness_errors.append("prepare_timeout")
            return
        if not prepared:
            return

        started = await self._timed("readiness", self._start())
        if started is TIMEOUT:
            self.observation.target_ready = False
            self.observation.harness_errors.append("target_unavailable")
            return
        if not started:
            self.observation.target_ready = False
            return

        executed = await self._timed(
            "execute",
            self._execute(),
            timeout_s=self.limits.execution_deadline_s + 2.0,
        )
        if executed is TIMEOUT:
            self.observation.execution_deadline_exceeded = True
            self.observation.harness_errors.append("execute_timeout")
            return

        await self._quiesce()

    async def _prepare(self) -> bool:
        profile = self.config.profile
        command = profile.format_command(
            profile.init_command,
            db=self.db_path,
            seed=profile.seed_path,
            host=self.config.host,
            port=self.port,
        )
        returncode, stdout, stderr = await self._run_command(command, _base_env(profile))
        if returncode != 0:
            self.observation.harness_errors.append("init_db_failed")
            self.observation.baseline_ok = False
            return False

        seed = load_seed(profile)
        try:
            baseline = TableSnapshot(tables=read_tables(self.db_path))
        except sqlite3.Error:
            self.observation.harness_errors.append("baseline_unreadable")
            self.observation.baseline_ok = False
            return False
        self.observation.baseline_ok = baseline.tables == seed.tables()
        if not self.observation.baseline_ok:
            self.observation.harness_errors.append("baseline_mismatch")
            return False

        schema_ok, error = probe_schema(self.db_path)
        if not schema_ok:
            self.observation.schema_ok = False
            self.observation.schema_error = error
            self.observation.harness_errors.append("schema_mismatch")
            return False

        self.config.stub.create_namespace(
            self.trial_id,
            self.config.scenario.fixtures,
            **_stub_configuration(self.config.scenario),
        )
        return True

    async def _start(self) -> bool:
        profile = self.config.profile
        env = _base_env(profile)
        env.update(
            {
                "DATABASE_PATH": str(self.db_path),
                "PROVIDER_BASE_URL": f"{self.config.stub.base_url}/t/{self.trial_id}",
                "WEBHOOK_SECRET": self.secret,
                "DURESS_INSTANCE": self.spawned_instance,
            }
        )
        command = profile.format_command(
            profile.serve_command,
            db=self.db_path,
            seed=profile.seed_path,
            host=self.config.host,
            port=self.port,
        )
        self.log_handle = open(self.workdir / "target.log", "wb")
        self.proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(profile.cwd),
            env=env,
            start_new_session=True,
            stdout=self.log_handle,
            stderr=asyncio.subprocess.STDOUT,
        )
        self.pgid = os.getpgid(self.proc.pid)
        write_pidfile(
            self.home,
            self.trial_id,
            Pidfile(
                pgid=self.pgid,
                leader=self.proc.pid,
                instance=self.trial_id,
                started_at=_now(),
                start_time=process_start_time(self.proc.pid),
            ),
        )
        self.pidfile_written = True

        deadline = time.monotonic() + self.limits.phase("readiness")
        url = f"http://{self.config.host}:{self.port}/healthz"
        while time.monotonic() < deadline:
            if self.proc.returncode is not None:
                return False
            try:
                async with httpx.AsyncClient(timeout=0.25) as client:
                    response = await client.get(url)
            except httpx.HTTPError:
                await asyncio.sleep(0.02)
                continue
            if response.status_code != 200:
                await asyncio.sleep(0.02)
                continue
            try:
                payload = response.json()
            except ValueError:
                await asyncio.sleep(0.02)
                continue
            if payload.get("instance") != self.expected_instance:
                self.observation.instance_ok = False
                self.observation.harness_errors.append("instance_mismatch")
                return False
            return True
        return False

    async def _execute(self) -> None:
        self.execution = await run_schedule(
            self.config.scenario.schedule,
            base_url=f"http://{self.config.host}:{self.port}",
            secret=self.secret,
            roles=self.config.scenario.roles,
            limits=self.limits,
            signature_header=self.config.profile.signing.header,
            event_id_header=self.config.profile.signing.event_id_header,
        )
        self.observation.attempts = self.execution.attempts
        self.observation.execution_deadline_exceeded = (
            self.execution.execution_deadline_exceeded
        )
        self.observation.cancelled_attempts = self.execution.cancelled_attempts
        self.observation.undispatched = self.execution.undispatched
        if self.execution.error:
            self.observation.harness_errors.append("runner_error")

    async def _quiesce(self) -> None:
        start = time.monotonic()
        deadline = start + self.limits.quiesce_s
        while time.monotonic() < deadline:
            try:
                stats = self.config.stub.stats(self.trial_id)
            except KeyError:
                break
            if stats["holding"] == 0:
                break
            await asyncio.sleep(0.01)
        self.phase_timings["quiesce"] = time.monotonic() - start
        try:
            if self.config.stub.stats(self.trial_id)["holding"]:
                self.observation.harness_errors.append("quiesce")
        except KeyError:
            pass

    # ------------------------------------------------------------------ #
    # Settlement
    # ------------------------------------------------------------------ #
    async def _settle(self) -> None:
        if self.proc is None:
            return
        crash_before_drain = self.proc.returncode is not None
        forced = False

        start = time.monotonic()
        if crash_before_drain:
            self._signal_group(signal.SIGKILL)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.observation.harness_errors.append("drain_timeout")
        else:
            self._signal_group(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=self.limits.phase("drain"))
            except asyncio.TimeoutError:
                forced = True
                self._signal_group(signal.SIGKILL)
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.observation.harness_errors.append("drain_timeout")
        self.phase_timings["drain"] = time.monotonic() - start

        exit_code = self.proc.returncode
        if crash_before_drain:
            settlement = "crashed"
        elif forced:
            settlement = "forced"
        elif exit_code != 0:
            settlement = "crashed"
        elif (
            self.execution.cancelled_attempts
            and not self.config.drain_verified
        ):
            settlement = "drain_unverified"
        else:
            settlement = "clean"
        self.observation.settlement = settlement

        start = time.monotonic()
        gone = await self._confirm_group_gone()
        self.phase_timings["kill_confirm"] = time.monotonic() - start
        if not gone:
            self.observation.settlement = "unconfirmed"
            self.observation.harness_errors.append(
                "unconfirmed:" + json.dumps(group_members(self.pgid or 0))
            )
            # Keep the pidfile so the startup sweep or the next operation can
            # reap the group. State is not read.
            return

        await self._evaluate()

    async def _confirm_group_gone(self) -> bool:
        if self.pgid is None:
            return True
        deadline = time.monotonic() + self.limits.phase("kill_confirm")
        while time.monotonic() < deadline:
            if not group_exists(self.pgid):
                return True
            await asyncio.sleep(0.02)
        return False

    async def _evaluate(self) -> None:
        start = time.monotonic()
        snapshot_dir = self.workdir / "snapshot"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self.db_path) + suffix)
            if source.exists():
                shutil.copy2(source, snapshot_dir / f"app.db{suffix}")
        snapshot_db = snapshot_dir / "app.db"
        try:
            tables = await asyncio.wait_for(
                asyncio.to_thread(read_tables, snapshot_db),
                timeout=self.limits.phase("evaluate"),
            )
            self.observation.snapshot = TableSnapshot(tables=tables)
            schema_ok, error = await asyncio.wait_for(
                asyncio.to_thread(probe_schema, snapshot_db),
                timeout=self.limits.phase("evaluate"),
            )
            self.observation.schema_ok = schema_ok
            self.observation.schema_error = error
        except asyncio.TimeoutError:
            self.observation.harness_errors.append("evaluation_timeout")
            self.observation.snapshot = None
        except (sqlite3.Error, OSError):
            self.observation.harness_errors.append("state_unreadable")
            self.observation.snapshot = None
        finally:
            self.phase_timings["evaluate"] = time.monotonic() - start
            if self.pidfile_written:
                remove_pidfile(self.home, self.trial_id)
                self.pidfile_written = False
            if self.log_handle is not None:
                try:
                    self.log_handle.close()
                except OSError:
                    pass
                self.log_handle = None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _timed(self, name: str, coro: Any, timeout_s: float | None = None) -> Any:
        start = time.monotonic()
        timeout = timeout_s if timeout_s is not None else self.limits.phase(name)
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            return TIMEOUT
        finally:
            self.phase_timings[name] = time.monotonic() - start

    async def _run_command(
        self, command: list[str], env: dict[str, str]
    ) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self.config.profile.cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        return process.returncode or 0, stdout, stderr

    def _signal_group(self, sig: int) -> None:
        if self.pgid is None:
            return
        try:
            os.killpg(self.pgid, sig)
        except ProcessLookupError:
            pass

    def _force_kill_sync(self) -> None:
        self._signal_group(signal.SIGKILL)

    def _stub_stats(self) -> dict[str, Any]:
        try:
            stats = self.config.stub.stats(self.trial_id)
        except KeyError:
            return {}
        stats["stale_calls"] = max(
            stats.get("stale_calls", 0),
            self.config.stub.registry.stale_total() - self.stale_baseline,
        )
        self.config.stub.retire(self.trial_id)
        return stats


async def run_trial(config: TrialConfig) -> TrialObservation:
    return await TrialSupervisor(config).run()