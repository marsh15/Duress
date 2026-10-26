"""Process-group liveness must never be inferred from a refused signal.

macOS ``killpg`` returns ``EPERM`` for groups whose members are zombies or
exiting, and has been observed to return it while a live member remains. If
that were read as termination, the supervisor would copy state while the target
was still writing. These tests pin the authoritative semantics.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from duress.store import (
    force_kill_members,
    group_exists,
    live_group_members,
    probe_group_members,
    sweep,
    write_pidfile,
    Pidfile,
)


def _spawn(code: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code], start_new_session=True)


def _wait_for(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_refused_signal_is_not_termination(monkeypatch):
    process = _spawn("import time; time.sleep(30)")
    pgid = os.getpgid(process.pid)
    try:
        assert group_exists(pgid)

        def refuse(*_args, **_kwargs):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(os, "killpg", refuse)
        # The group is alive; a refused signal says nothing about that.
        assert group_exists(pgid) is True
        assert live_group_members(pgid)
    finally:
        monkeypatch.undo()
        os.killpg(pgid, signal.SIGKILL)
        process.wait(timeout=5)


def test_zombie_only_group_is_not_alive(monkeypatch):
    process = _spawn("import time; time.sleep(30)")
    pgid = os.getpgid(process.pid)
    os.killpg(pgid, signal.SIGKILL)
    assert _wait_for(lambda: process.poll() is not None)
    # Not reaped yet: the member is a zombie and cannot run or write.
    members = probe_group_members(pgid)
    assert members is not None
    assert all(state.startswith("Z") for _, state, _ in members) or members == []
    assert group_exists(pgid) is False
    process.wait(timeout=5)


def test_per_pid_escalation_terminates_a_live_group(monkeypatch):
    process = _spawn(
        "import signal, subprocess, sys, time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
        "time.sleep(30)"
    )
    pgid = os.getpgid(process.pid)
    try:
        assert _wait_for(lambda: len(live_group_members(pgid)) >= 2)
        # Simulate the macOS refusal: the group signal never lands.
        monkeypatch.setattr(os, "killpg", lambda *a, **k: None)
        attempted = force_kill_members(pgid)
        assert attempted
        assert _wait_for(lambda: not group_exists(pgid), timeout=5)
    finally:
        monkeypatch.undo()
        for pid, _state, _comm in live_group_members(pgid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        process.wait(timeout=5)


def test_force_kill_never_signals_our_own_group():
    assert force_kill_members(os.getpgrp()) == []
    assert force_kill_members(0) == []


def test_sweep_keeps_the_pidfile_when_a_group_will_not_die(tmp_path, monkeypatch):
    """A leaked group must not be reported as swept."""
    home = tmp_path / ".duress"
    home.mkdir()
    process = _spawn("import time; time.sleep(30)")
    pgid = os.getpgid(process.pid)
    try:
        from duress.store import process_start_time

        write_pidfile(
            home,
            "t-stubborn",
            Pidfile(
                pgid=pgid,
                leader=process.pid,
                instance="t-stubborn",
                started_at="2026-01-01T00:00:00+00:00",
                start_time=process_start_time(process.pid),
            ),
        )
        monkeypatch.setattr(os, "killpg", lambda *a, **k: None)
        monkeypatch.setattr("duress.store.force_kill_members", lambda *a, **k: [])
        result = sweep(home, timeout_s=0.1)
        assert result.killed == []
        assert [item.instance for item in result.unresolved] == ["t-stubborn"]
        assert (home / "run" / "t-stubborn.pid").exists()
    finally:
        monkeypatch.undo()
        os.killpg(pgid, signal.SIGKILL)
        process.wait(timeout=5)
