"""Target launcher that honours the drain contract (plan C.1).

``python -m uvicorn`` is not used directly because uvicorn re-raises the captured
SIGTERM after a graceful shutdown, so the process exits with status -15. Target
contract v1 requires "on SIGTERM, stop accepting, finish in-flight requests,
exit 0". This launcher keeps uvicorn's graceful drain and suppresses the
re-raise.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import signal
import threading
from typing import Iterator

import uvicorn
from uvicorn.server import HANDLED_SIGNALS


class GracefulServer(uvicorn.Server):
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
        # Deliberately no signal.raise_signal(): the drain contract says exit 0.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m razorpay_ref.serve")
    parser.add_argument("--variant", required=True, choices=("vulnerable", "fixed"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--workers", type=int, default=1, help="ASGI worker processes (E8)")
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args(argv)

    if args.workers > 1:
        # uvicorn.run's supervisor handles SIGTERM for the whole worker set and
        # returns normally, so the process exits 0 after a graceful drain.
        uvicorn.run(
            f"razorpay_ref.{args.variant}.app:app",
            host=args.host,
            port=args.port,
            workers=args.workers,
            log_level=args.log_level,
            access_log=False,
        )
        return 0

    module = importlib.import_module(f"razorpay_ref.{args.variant}.app")
    config = uvicorn.Config(
        module.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )
    GracefulServer(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
