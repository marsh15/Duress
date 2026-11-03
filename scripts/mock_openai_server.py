"""Local OpenAI-compatible mock of ``POST /v1/chat/completions``.

This server exists to exercise the live investigator path without a provider.
Every reply is scripted by the caller: status, body, latency and content.
Nothing here is a model, so any result produced with this server is a
**mock-provider** result and must be labelled as such.

Replies are consumed in order; the last reply repeats once the script is
exhausted. Every request is recorded (path, lower-cased headers, parsed body)
so tests can assert what the client sent, including the Authorization header.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHAT_PATH = "/v1/chat/completions"
VULNERABLE_HANDLER = ROOT / "targets" / "razorpay_ref" / "vulnerable" / "handler.py"


@dataclass(frozen=True)
class Reply:
    """One scripted HTTP reply.

    ``content`` is wrapped in a normal chat-completions envelope. ``raw_body``
    is sent verbatim instead (for HTML, empty or otherwise malformed bodies).
    """

    status: int = 200
    content: str | None = None
    raw_body: str | None = None
    prompt_tokens: int | None = 900
    delay_s: float = 0.0
    content_type: str = "application/json"

    @classmethod
    def ok(cls, content: str, *, prompt_tokens: int | None = 900, delay_s: float = 0.0) -> "Reply":
        return cls(status=200, content=content, prompt_tokens=prompt_tokens, delay_s=delay_s)

    @classmethod
    def error(cls, status: int, body: str = "", *, delay_s: float = 0.0) -> "Reply":
        return cls(status=status, raw_body=body, content_type="text/plain", delay_s=delay_s)

    def payload(self) -> tuple[bytes, str]:
        if self.raw_body is not None:
            return self.raw_body.encode(), self.content_type
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        envelope: dict[str, Any] = {"choices": [{"index": 0, "message": message}]}
        if self.prompt_tokens is not None:
            envelope["usage"] = {"prompt_tokens": self.prompt_tokens, "completion_tokens": 1}
        return json.dumps(envelope).encode(), "application/json"


@dataclass(frozen=True)
class RecordedRequest:
    path: str
    headers: dict[str, str]
    body: Any


class MockOpenAIServer:
    """Context manager running the mock on 127.0.0.1 with an ephemeral port."""

    def __init__(self, replies: list[Reply]) -> None:
        if not replies:
            raise ValueError("MockOpenAIServer needs at least one reply")
        self._replies = list(replies)
        self._lock = threading.Lock()
        self.requests: list[RecordedRequest] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def _next_reply(self, request: RecordedRequest) -> Reply:
        with self._lock:
            self.requests.append(request)
            index = min(len(self.requests) - 1, len(self._replies) - 1)
            return self._replies[index]

    def __enter__(self) -> "MockOpenAIServer":
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return  # keep test and dry-run output quiet

            def do_POST(self) -> None:  # noqa: N802 - http.server naming
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body: Any = json.loads(raw) if raw else None
                except ValueError:
                    body = raw.decode(errors="replace")
                headers = {key.lower(): value for key, value in self.headers.items()}
                if self.path != CHAT_PATH:
                    self._send(404, b'{"error":"not found"}', "application/json")
                    return
                reply = server._next_reply(RecordedRequest(self.path, headers, body))
                if reply.delay_s:
                    time.sleep(reply.delay_s)
                payload, content_type = reply.payload()
                self._send(reply.status, payload, content_type)

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client gave up (timeout); nothing to report

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None


def vulnerable_proposal(**hypothesis_overrides: Any) -> dict[str, Any]:
    """A schema-valid proposal whose citations match the bundled vulnerable handler.

    Line numbers come from the handler source, so the citation check passes
    against the real bundle. ``hypothesis_overrides`` patch the single hypothesis
    for negative cases.
    """
    lines = VULNERABLE_HANDLER.read_text().splitlines()

    def line_of(needle: str) -> int:
        return next(index for index, line in enumerate(lines, 1) if needle in line)

    hypothesis: dict[str, Any] = {
        "id": "H1",
        "title": "Status check and fulfilment insert are not atomic",
        "mechanism": "Two deliveries read 'not captured' before either commits.",
        "mechanism_kind": "check_then_act_race",
        "citations": [
            {
                "file": "razorpay_ref/vulnerable/handler.py",
                "line_start": line_of('"SELECT status, amount, currency FROM payments'),
                "line_end": line_of('"INSERT INTO fulfilments'),
                "quote": "INSERT INTO fulfilments",
            }
        ],
        "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
        "scenarios": [{"template_id": "S3", "params": {"copies": 3, "fault": "rendezvous"}}],
    }
    hypothesis.update(hypothesis_overrides)
    return {"hypotheses": [hypothesis]}


def proposal_text(proposal: dict[str, Any]) -> str:
    return json.dumps(proposal)
