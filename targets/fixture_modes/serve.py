"""Launcher for fixture modes, including the deliberately bad shutdown modes."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import signal
import sys
import threading
from typing import Iterator

import uvicorn
from uvicorn.server import HANDLED_SIGNALS

from . import MODES

HANG_MODES = {"hang_on_shutdown", "dup_then_hang"}


class GracefulServer(uvicorn.Server):
    """Drain on SIGTERM and exit 0 (same contract as the reference target)."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        originals = {sig: signal.signal(sig, self.handle_exit) for sig in HANDLED_SIGNALS}
        try:
            yield
        finally:
            for sig, handler in originals.items():
                signal.signal(sig, handler)


class HangServer(GracefulServer):
    """Ignore SIGTERM; only SIGKILL ends the process."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        originals = {sig: signal.signal(sig, signal.SIG_IGN) for sig in HANDLED_SIGNALS}
        try:
            yield
        finally:
            for sig, handler in originals.items():
                signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fixture_modes.serve")
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args(argv)

    if args.mode == "crash_on_boot":
        sys.stderr.write("fixture_modes: crash_on_boot\n")
        return 1

    os.environ["FIXTURE_MODE"] = args.mode
    module = importlib.import_module("fixture_modes.app")
    config = uvicorn.Config(
        module.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )
    server_class = HangServer if args.mode in HANG_MODES else GracefulServer
    server_class(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())