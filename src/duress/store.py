"""JSON persistence, atomic writes and the startup pidfile sweep (C.2.2)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


def duress_home(root: Path | None = None) -> Path:
    override = os.environ.get("DURESS_HOME")
    if override:
        return Path(override)
    return Path(root or Path.cwd()) / ".duress"


def ensure_dirs(home: Path) -> None:
    for name in ("investigations", "dna", "evidence", "run", "reductions", "differentials"):
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


def _proc_group_members(pgid: int) -> list[tuple[int, str, str]] | None:
    """Members of a process group from ``/proc``, or None when unavailable.

    Reads the process state as well as the pgid. A zombie keeps its pgid, so a
    signal probe can succeed after every process has exited; in a container
    PID 1 may not reap adopted children promptly.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    members: list[tuple[int, str, str]] = []
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
        if fields[2] != str(pgid):
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            comm = ""
        members.append((int(entry.name), fields[0], comm))
    return members


def _ps_group_members(pgid: int) -> list[tuple[int, str, str]] | None:
    """Members of a process group from ``ps``, or None when unavailable.

    ``python:3.12-slim`` has no ``ps``; Linux therefore uses ``/proc``. macOS
    has no ``/proc`` and this is the only portable way to read a state.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,pgid=,stat=,comm=", "-ax"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    members: list[tuple[int, str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3 or parts[1] != str(pgid):
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        members.append((pid, parts[2], parts[3].strip() if len(parts) > 3 else ""))
    return members


def probe_group_members(pgid: int) -> list[tuple[int, str, str]] | None:
    """Every member (pid, state, command), or None if no probe is available."""
    if pgid <= 0:
        return []
    members = _proc_group_members(pgid)
    if members is not None:
        return members
    return _ps_group_members(pgid)


def group_members(pgid: int) -> list[tuple[int, str, str]]:
    """Live and zombie members of a process group, for diagnostics."""
    return probe_group_members(pgid) or []


def live_group_members(pgid: int) -> list[tuple[int, str, str]]:
    """Members that can still execute user code (state is not ``Z``)."""
    return [member for member in group_members(pgid) if not member[1].startswith("Z")]


def group_exists(pgid: int) -> bool:
    """Authoritative liveness: is any process in the group still runnable?

    ``killpg`` is deliberately not used for liveness. On macOS it reports
    ``EPERM`` while members are zombies or exiting, and after some signals even
    when a live member remains. Treating that as termination would authorise
    reading state while the target is still writing, so liveness comes from the
    process table and EPERM is only ever treated as a failed signal attempt.
    """
    members = probe_group_members(pgid)
    if members is not None:
        return any(not state.startswith("Z") for _, state, _ in members)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # No process table to consult and the signal probe was refused. Staying
        # on the safe side (a live group) forces an unconfirmed settlement
        # rather than reading possibly in-flight state.
        return True
    return True


def force_kill_members(pgid: int, *, sig: int = signal.SIGKILL) -> list[int]:
    """Signal each live member directly and return the pids attempted.

    ``killpg`` can be refused on macOS with ``EPERM`` even though a member is
    alive, which would leave a target running past its trial. Signalling the
    pids read from the process table is the escalation that keeps the
    termination guarantee. The caller still confirms through ``group_exists``.
    """
    if pgid <= 0 or pgid == os.getpgrp():
        # Never signal our own process group, even if a pidfile is corrupt.
        return []
    attempted: list[int] = []
    own = os.getpid()
    for pid, state, _ in live_group_members(pgid):
        if pid in {own, 0} or state.startswith("Z"):
            continue
        try:
            os.kill(pid, sig)
            attempted.append(pid)
        except (ProcessLookupError, PermissionError):
            continue
    return attempted


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
    unresolved: list[Pidfile] = field(default_factory=list)


def _wait_group_gone(pgid: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not group_exists(pgid):
            return True
        time.sleep(0.02)
    return not group_exists(pgid)


def sweep(home: Path, *, timeout_s: float = 5.0) -> SweepResult:
    """Kill groups whose recorded start time still matches; drop stale records.

    A pidfile whose leader start time no longer matches is never killed: the pid
    may have been reused by an unrelated process. A pidfile whose group is still
    alive after escalation is kept, so a later sweep retries instead of
    pretending the leak was cleaned up.
    """
    killed: list[Pidfile] = []
    stale: list[Pidfile] = []
    unresolved: list[Pidfile] = []
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
        except (ProcessLookupError, PermissionError):
            # A refused signal is not proof of termination. Confirmation below
            # reads the process table.
            pass
        if _wait_group_gone(info.pgid, timeout_s):
            killed.append(info)
            path.unlink(missing_ok=True)
            continue
        try:
            os.killpg(info.pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        force_kill_members(info.pgid)
        if _wait_group_gone(info.pgid, 2.0):
            killed.append(info)
            path.unlink(missing_ok=True)
        else:
            unresolved.append(info)
    return SweepResult(killed=killed, stale=stale, unresolved=unresolved)


def copy_evidence(source: Path, home: Path, trial_id: str) -> Path:
    destination = home / "evidence" / trial_id
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination

# --------------------------------------------------------------------------- #
# Failure DNA and investigations
# --------------------------------------------------------------------------- #
def dna_path(home: Path, dna_id: str) -> Path:
    return home / "dna" / f"{dna_id}.json"


def save_dna(home: Path, document: Any) -> Path:
    payload = (
        document.model_dump(mode="json")
        if hasattr(document, "model_dump")
        else document
    )
    path = dna_path(home, payload["dna_id"])
    atomic_write_json(path, payload)
    return path


def load_dna_payload(home: Path, dna_id: str) -> Any:
    from .records import read_record

    # Preserve the raw loaded mapping for digest preflight; do not normalize it.
    return read_record(home / "dna", dna_id)


def list_dna(home: Path) -> list[dict[str, Any]]:
    directory = home / "dna"
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = load_dna_payload(home, path.stem)
        except FileNotFoundError:
            continue
        if not isinstance(payload.get("reproducer"), dict) or any(
            key in payload and not isinstance(payload[key], dict)
            for key in ("characterization", "provenance_scenario", "provenance")
        ):
            continue
        items.append(
            {
                "dna_id": payload.get("dna_id"),
                "signature": payload.get("reproducer", {}).get("failure_signature"),
                "control_level": payload.get("reproducer", {}).get("control_level"),
                "p0": payload.get("characterization", {}).get("p0"),
                "template_id": payload.get("provenance_scenario", {}).get("template_id"),
                "created_at": payload.get("provenance", {}).get("created_at"),
                "reduction": payload.get("reduction"),
            }
        )
    return items
