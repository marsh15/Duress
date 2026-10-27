"""External lifecycle adapter reusing the trusted supervisor's settlement.

Only application preparation, readiness, delivery and observation differ.
SIGTERM/drain/escalation/process-table confirmation remain TrialSupervisor's.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import shutil
import sys
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..limits import DEFAULT_LIMITS
from ..provider_stub import free_port
from ..runner import run_schedule
from ..store import Pidfile, force_kill_members, group_exists, group_members, process_start_time, remove_pidfile, write_pidfile
from ..target import TrialConfig, TrialSupervisor, _now
from .manifest import LoadedProject, Scenario


@dataclass(frozen=True)
class ExternalProfile:
    target_id: str
    cwd: Path


class NoProvider:
    """No payment-provider namespace participates in an external trial."""
    @property
    def registry(self):
        return self

    def stale_total(self):
        return 0


class ExternalSupervisor(TrialSupervisor):
    def __init__(self, project: LoadedProject, scenario: Scenario, home: Path):
        self.project = project
        self.scenario = scenario
        self.before: Any = None
        self.after: Any = None
        home.mkdir(parents=True, exist_ok=True)
        # TrialSupervisor only needs target_id/cwd at construction. Adapter
        # hooks below replace domain-dependent phases; settlement is inherited.
        config = TrialConfig(
            profile=ExternalProfile(project.manifest.target.id, project.cwd),
            scenario=type("ScenarioIdentity", (), {"scenario_id": scenario.id})(),
            stub=NoProvider(), workdir_root=home, home=home,
        )
        super().__init__(config)
        self.schema = ("duress_" + self.trial_id
                       if project.manifest.observation.adapter == "postgresql@1" else "main")
        self.database = str(self.db_path)
        self.observer_database = self.database
        self.init_pgid: int | None = None
        self.last_memory_probe = 0.0
        self.env = self._environment()

    def _environment(self) -> dict[str, str]:
        target = self.project.manifest.target
        observation = self.project.manifest.observation
        env = {k: os.environ[k] for k in ("PATH", "LANG", "TZ", "SYSTEMROOT") if k in os.environ}
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        env.update(target.env)
        env.update({name: self.project.resolve(ref) for name, ref in target.env_refs.items()})
        if observation.adapter == "postgresql@1":
            self.database = self.project.resolve(observation.database_ref)
            self.observer_database = self.project.resolve(observation.observer_ref or observation.database_ref)
        env.update(DURESS_DB=self.database, DURESS_SCHEMA=self.schema,
                   DURESS_HOST="127.0.0.1", DURESS_PORT=str(self.port),
                   DURESS_INSTANCE=self.trial_id, DURESS_SECRET=self.secret,
                   DURESS_FIXTURES=str(self.project.fixtures_path), PYTHONUNBUFFERED="1")
        return env

    def command(self, command: list[str]) -> list[str]:
        argv = [sys.executable if part == "python" and i == 0 else part for i, part in enumerate(command)]
        target = self.project.manifest.target
        return [sys.executable, str(Path(__file__).with_name("launch.py")),
                str(target.memory_mb), str(target.max_processes), *argv]

    async def _prepare(self) -> bool:
        from .observers import observe
        command = self.command(self.project.manifest.target.init)
        self.log_handle = open(self.workdir / "target.log", "wb")
        proc = await asyncio.create_subprocess_exec(*command, cwd=self.project.cwd, env=self.env,
            start_new_session=True, stdout=self.log_handle, stderr=asyncio.subprocess.STDOUT)
        self.init_pgid = proc.pid
        write_pidfile(self.home, self.trial_id + "-init", Pidfile(proc.pid, proc.pid, self.trial_id,
                      _now(), process_start_time(proc.pid)))
        try:
            while proc.returncode is None:
                self._check_group_limits(self.init_pgid)
                await asyncio.sleep(.025)
        finally:
            # An init command can fork too. Never read a baseline until all its
            # descendants have stopped, including on timeout/cancellation.
            cleanup = asyncio.create_task(self._cleanup_init(proc))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.shield(cleanup)
                raise
        if group_exists(self.init_pgid):
            self.observation.harness_errors.append("init_group_unconfirmed")
            return False
        remove_pidfile(self.home, self.trial_id + "-init")
        if proc.returncode != 0:
            self.observation.harness_errors.append("init_failed")
            return False
        observation = self.project.manifest.observation
        self.before = await asyncio.to_thread(observe, self.project.contract,
            kind=observation.adapter.split("@")[0], database=self.observer_database,
            schema=self.schema, settled=True)
        if (not self.before.available or not self.before.valid
                or set(self.before.entities) != set(self.project.contract.entities)):
            self.observation.harness_errors.append("baseline_unavailable")
            return False
        if (self.project.expected_schema is not None
                and self.before.provenance.get("schema_fingerprint") != self.project.expected_schema):
            self.observation.harness_errors.append("replay_schema_incompatible")
            return False
        return True

    async def _cleanup_init(self, proc):
        deadline = time.monotonic() + self.limits.phase("kill_confirm")
        force_kill_members(self.init_pgid)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            self.observation.harness_errors.append("init_leader_unconfirmed")
        while group_exists(self.init_pgid) and time.monotonic() < deadline:
            force_kill_members(self.init_pgid)
            await asyncio.sleep(.025)
        if group_exists(self.init_pgid):
            self.observation.harness_errors.append("init_group_unconfirmed")
        else:
            remove_pidfile(self.home, self.trial_id + "-init")

    async def _start(self) -> bool:
        self.proc = await asyncio.create_subprocess_exec(*self.command(self.project.manifest.target.serve),
            cwd=self.project.cwd, env=self.env, start_new_session=True,
            stdout=self.log_handle, stderr=asyncio.subprocess.STDOUT)
        self.pgid = self.proc.pid
        write_pidfile(self.home, self.trial_id, Pidfile(self.pgid, self.proc.pid, self.trial_id,
                      _now(), process_start_time(self.proc.pid)))
        self.pidfile_written = True
        async with httpx.AsyncClient(timeout=.25, follow_redirects=False, trust_env=False) as client:
            while self.proc.returncode is None:
                self._check_processes()
                try:
                    response = await client.get(self.base_url + self.project.manifest.target.readiness_path)
                    if response.status_code == 200 and response.json().get("instance") == self.trial_id:
                        return True
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(.02)
        return False

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def _check_processes(self):
        self._check_group_limits(self.pgid)

    def _check_group_limits(self, pgid):
        members = group_members(pgid) if pgid else []
        if len(members) > self.project.manifest.target.max_processes:
            self.observation.harness_errors.append("process_limit_exceeded")
            raise RuntimeError("process_limit_exceeded")
        if not members or time.monotonic() - self.last_memory_probe < .25:
            return
        self.last_memory_probe = time.monotonic()
        pids = [str(pid) for pid,state,_ in members if not state.startswith("Z")]
        if not pids:
            return
        if Path("/proc").is_dir():
            rss = 0
            for pid in pids:
                try:
                    rss += int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                except FileNotFoundError:
                    pass
        else:
            probe = subprocess.run(["ps","-o","rss=","-p",",".join(pids)],
                                   capture_output=True,text=True,timeout=1)
            if probe.returncode and group_exists(pgid):
                self.observation.harness_errors.append("memory_probe_unavailable")
                raise RuntimeError("memory_probe_unavailable")
            rss = sum(int(row) * 1024 for row in probe.stdout.split())
        if rss > self.project.manifest.target.memory_mb * 1024**2:
            self.observation.harness_errors.append("memory_limit_exceeded")
            raise RuntimeError("memory_limit_exceeded")

    def request(self, delivery, attempt):
        spec = self.project.manifest.delivery
        body = self.project.payloads[delivery.fixture]
        headers = {"Content-Type": "application/json", **spec.headers,
                   **{name: self.project.resolve(ref) for name, ref in spec.header_refs.items()},
                   spec.event_id_header: delivery.event_id}
        if spec.signature == "hmac-sha256":
            signature = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
            headers[spec.signature_header] = "invalid" if delivery.invalid_signature else signature
        return spec.method, self.base_url + spec.endpoint, body, headers

    async def _execute(self):
        task = asyncio.create_task(run_schedule(self.scenario, base_url=self.base_url,
            secret=self.secret, roles={}, limits=self.limits, request_builder=self.request))
        try:
            while not task.done():
                self._check_processes()
                await asyncio.sleep(.025)
            self.execution = await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.observation.attempts = self.execution.attempts
        self.observation.execution_deadline_exceeded = self.execution.execution_deadline_exceeded
        self.observation.cancelled_attempts = self.execution.cancelled_attempts
        self.observation.undispatched = self.execution.undispatched
        if self.execution.error:
            self.observation.harness_errors.append("runner_error")

    async def _quiesce(self):
        await asyncio.sleep(self.scenario.settle_ms / 1000)

    async def _evaluate(self):
        from .observers import observe
        observation = self.project.manifest.observation
        self.after = await asyncio.to_thread(observe, self.project.contract,
            kind=observation.adapter.split("@")[0], database=self.observer_database,
            schema=self.schema, settled=self.observation.settlement == "clean")
        if self.pidfile_written:
            remove_pidfile(self.home, self.trial_id)
            self.pidfile_written = False

    def _stub_stats(self):
        return {}

    async def run(self):
        try:
            return await super().run()
        finally:
            # Keep normalized allowlisted evidence only. Application logs and
            # raw databases may contain fields outside the observation scope.
            if self.log_handle:
                self.log_handle.close()
                self.log_handle = None
            if not group_exists(self.pgid or 0) and not group_exists(self.init_pgid or 0):
                if self.project.manifest.observation.adapter == "postgresql@1":
                    await asyncio.to_thread(self._drop_schema)
                shutil.rmtree(self.workdir, ignore_errors=True)

    def _drop_schema(self):
        import psycopg
        from psycopg import sql
        try:
            with psycopg.connect(self.database, connect_timeout=3, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))
        except psycopg.Error:
            self.observation.harness_errors.append("test_schema_cleanup_failed")
