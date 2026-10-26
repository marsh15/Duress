"""Small network/process helpers shared by the stub and the harness."""

from __future__ import annotations

import socket
import time

import httpx


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_http(url: str, timeout_s: float = 10.0, interval_s: float = 0.05) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=0.5)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError as exc:  # not up yet
            last_error = exc
        time.sleep(interval_s)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")
