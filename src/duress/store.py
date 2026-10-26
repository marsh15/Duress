"""JSON persistence, atomic writes and the startup pidfile sweep (C.2.2)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


def duress_home(root: Path | None = None) -> Path:
    override = os.environ.get("DURESS_HOME")
    if override:
        return Path(override)
    return Path(root or Path.cwd()) / ".duress"


def ensure_dirs(home: Path) -> None:
    for name in ("investigations", "dna", "evidence", "run"):
        (home / name).mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _linux_start_time(pid: int) -> str | None:
    """Read the kernel start time without requiring procps.

    ``python:3.12-slim`` has no ``ps``, and the release container must still be
    able to sweep its own leaked groups.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        boot = Path("/proc/stat").read_text()
    except OSError:
        return None
    _, _, remainder = stat.rpartition(")")
    fields = remainder.split()
    if len(fields) < 20:
        return None
    start_ticks = fields[19]
    boot_seconds = next(
        (line.split()[1] for line in boot.splitlines() if line.startswith("btime ")),
        None,
    )
    if boot_seconds is None:
        return None
    return f"proc:{boot_seconds}:{start_ticks}"


def process_start_time(pid: int) -> str | None:
    """Recorded start time used to distinguish a live group from a reused pid."""
    linux = _linux_start_time(pid)
    if linux is not None:
        return linux
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _linux_group_alive(pgid: int) -> bool | None:
    """Live (non-zombie) members of a process group, or None when unavailable.

    A zombie keeps its pgid, so ``killpg(pgid, 0)`` can succeed after every
    process has exited. In a container PID 1 may not reap adopted children
    promptly, so confirmation reads the process state instead of trusting the
    signal probe alone.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    alive = False
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        _, _, remainder = stat.rpartition(")")
        fields = remainder.split()
        if len(fields) < 3:
            continue
        state = fields[0]
        pgrp = fields[2]
        if pgrp == str(pgid) and state != "Z":
            alive = True
            break
    return alive


def group_members(pgid: int) -> list[tuple[int, str, str]]:
    """Live and zombie members of a process group, for diagnostics."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    members: list[tuple[int, str, str]] = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            comm = (entry / "comm").read_text().strip()
        except OSError:
            continue
        _, _, remainder = stat.rpartition(")")
        fields = remainder.split()
        if len(fields) < 3:
            continue
        if fields[2] == str(pgid):
            members.append((int(entry.name), fields[0], comm))
    return members


def group_exists(pgid: int) -> bool:
    live = _linux_group_alive(pgid)
    if live is not None:
        return live
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS briefly reports EPERM for a group that has already exited but
        # not yet been reaped. The group is no longer ours to manage.
        return False
    return True


@dataclass(frozen=True)
class Pidfile:
    pgid: int
    leader: int
    instance: str
    started_at: str
    start_time: str | None = None


def write_pidfile(home: Path, trial_id: str, pidfile: Pidfile) -> Path:
    ensure_dirs(home)
    path = home / "run" / f"{trial_id}.pid"
    atomic_write_json(path, asdict(pidfile))
    return path


def remove_pidfile(home: Path, trial_id: str) -> None:
    path = home / "run" / f"{trial_id}.pid"
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def iter_pidfiles(home: Path) -> Iterable[Path]:
    directory = home / "run"
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.pid"))


@dataclass(frozen=True)
class SweepResult:
    killed: list[Pidfile]
    stale: list[Pidfile]


def sweep(home: Path, *, timeout_s: float = 5.0) -> SweepResult:
    """Kill groups whose recorded start time still matches; drop stale records.

    A pidfile whose leader start time no longer matches is never killed: the pid
    may have been reused by an unrelated process.
    """
    killed: list[Pidfile] = []
    stale: list[Pidfile] = []
    for path in list(iter_pidfiles(home)):
        try:
            payload = read_json(path)
            info = Pidfile(**payload)
        except (OSError, TypeError, ValueError):
            path.unlink(missing_ok=True)
            continue
        if not group_exists(info.pgid):
            path.unlink(missing_ok=True)
            continue
        current = process_start_time(info.leader)
        if info.start_time is None or current != info.start_time:
            stale.append(info)
            path.unlink(missing_ok=True)
            continue
        try:
            os.killpg(info.pgid, signal.SIGTERM)
        except ProcessLookupError:
            path.unlink(missing_ok=True)
            continue
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and group_exists(info.pgid):
            time.sleep(0.02)
        if group_exists(info.pgid):
            try:
                os.killpg(info.pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        killed.append(info)
        path.unlink(missing_ok=True)
    return SweepResult(killed=killed, stale=stale)


def copy_evidence(source: Path, home: Path, trial_id: str) -> Path:
    destination = home / "evidence" / trial_id
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination