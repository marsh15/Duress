"""Local fake staging application, forward proxy and manifest builder for staging tests.

The fake staging server is a real HTTP(S) listener on 127.0.0.1 with an ephemeral
port. Its ``attest`` mode controls the cooperative quiescence endpoint so tests
can exercise honest, absent, replayed, negative and drifting behaviour.
"""
from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from duress.external.manifest import LoadedProject, load_project

CONTRACT = {
    "id": "effects-v1",
    "version": 1,
    "entities": {"effects": {"table": "effects", "key": "id", "columns": ["id", "entity"]}},
    "invariants": [{"id": "unique-effect", "kind": "unique_by", "entity": "effects", "keys": ["entity"]}],
}
FIXTURES = {"events": {"paid": {"payload": {"entity": "owned"}}}}
TEMPLATE = """\
[duress]
schema_version = 1
project = "staging-proj"

[target]
id = "staging-target"
mode = "staging"
base_url = "{origin}"
allowed_origins = ["{origin}"]
staging_authorized = true
isolated_test_database = true
{target_extra}
{quiescence}
[delivery]
signature = "none"

[observation]
adapter = "postgresql@1"
database_ref = "DURESS_TEST_DB"

[contract]
file = "contract.json"

[fixtures]
file = "fixtures.json"

[[scenarios]]
id = "one-delivery"
deliveries = [{{id = "one", fixture = "paid", event_id = "e1"}}]
steps = [{{deliveries = ["one"]}}]
expected_status = {{one = "2xx"}}
"""


def write_manifest(directory: Path, origin: str, *, quiescence: str = "", target_extra: str = "",
                   files: dict[str, bytes] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "contract.json").write_text(json.dumps(CONTRACT))
    (directory / "fixtures.json").write_text(json.dumps(FIXTURES))
    for name, content in (files or {}).items():
        (directory / name).write_bytes(content)
    path = directory / "duress.toml"
    path.write_text(TEMPLATE.format(origin=origin, quiescence=quiescence, target_extra=target_extra))
    return path


QUIESCENCE_TABLE = """\
[target.quiescence]
path = "/duress/quiescence"
stable_reads = 3
interval_ms = 20
timeout_ms = 600
"""


def load_staging(directory: Path, origin: str, **kwargs) -> LoadedProject:
    return load_project(write_manifest(directory, origin, **kwargs))


class FakeStaging:
    """Local staging application with a controllable cooperative quiescence endpoint.

    ``attest`` modes: ``honest`` attests with the echoed nonce; ``always_200``
    returns 2xx with no attestation; ``missing`` has no endpoint (404);
    ``replay`` echoes a stale nonce; ``not_quiescent`` reports busy; ``drift``
    attests but keeps writing, so observed state never settles.
    """

    def __init__(self, attest: str = "honest", tls: tuple[str, str] | None = None):
        self.attest = attest
        # When set, the attestation mode switches after the first delivery POST.
        self.post_attest: str | None = None
        self.effects: list[dict] = []
        self.posts: list[str] = []
        self.probes = 0
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _staging_handler(self))
        if tls is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*tls)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.scheme = "https" if tls is not None else "http"
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.port}"

    def snapshot_rows(self) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.effects]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _staging_handler(app: FakeStaging):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: bytes, content_type: str = "application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path != "/duress/quiescence":
                return self._send(404, b"{}")
            with app.lock:
                app.probes += 1
            nonce = parse_qs(url.query).get("nonce", [""])[0]
            if app.attest == "always_200":
                return self._send(200, b"ok", "text/plain")
            if app.attest == "honest":
                return self._send(200, json.dumps({"quiescent": True, "nonce": nonce}).encode())
            if app.attest == "drift":
                with app.lock:
                    app.effects.append({"id": len(app.effects) + 1, "entity": f"drift-{len(app.effects)}"})
                return self._send(200, json.dumps({"quiescent": True, "nonce": nonce}).encode())
            if app.attest == "replay":
                return self._send(200, json.dumps({"quiescent": True, "nonce": "stale-nonce"}).encode())
            if app.attest == "not_quiescent":
                return self._send(200, json.dumps({"quiescent": False, "nonce": nonce}).encode())
            return self._send(404, b"{}")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            with app.lock:
                app.posts.append(self.path)
                if self.path == "/webhooks":
                    entity = json.loads(body)["entity"]
                    app.effects.append({"id": len(app.effects) + 1, "entity": entity})
                if app.post_attest:
                    app.attest = app.post_attest
            self._send(200, b"{}")

    return Handler


class ForwardProxy:
    """Minimal absolute-form HTTP proxy that records requests and forwards GET and POST."""

    def __init__(self):
        self.requests: list[str] = []
        self.lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _forward(self, data: bytes | None):
                with proxy.lock:
                    proxy.requests.append(self.path)
                headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
                request = urllib.request.Request(self.path, data=data, headers=headers,
                                                 method=self.command)
                try:
                    with proxy._opener.open(request, timeout=2) as upstream:
                        status, body = upstream.status, upstream.read()
                except urllib.error.HTTPError as exc:
                    status, body = exc.code, exc.read()
                except Exception:
                    status, body = 502, b"{}"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._forward(None)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self._forward(self.rfile.read(length))

        return Handler


def make_self_signed(directory: Path) -> tuple[Path, Path] | None:
    """Create a localhost certificate with the openssl CLI, or None when unavailable."""
    import shutil
    import subprocess

    if shutil.which("openssl") is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "cert.pem", directory / "key.pem"
    result = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(cert), "-days", "1", "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1"],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return None
    return cert, key
