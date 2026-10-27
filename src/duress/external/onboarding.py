"""A complete runnable starter in an owned directory outside Duress."""
import json
from pathlib import Path

from .manifest import Project

STARTER_APP = '''import hashlib, hmac, json, os, signal, sqlite3, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

database = os.environ["DURESS_DB"]
if sys.argv[1] == "init":
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE activations (id INTEGER PRIMARY KEY, resource TEXT UNIQUE NOT NULL)")
    sys.exit(0)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"instance":os.environ["DURESS_INSTANCE"]}).encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        signature = hmac.new(os.environ["DURESS_SECRET"].encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, self.headers.get("X-Signature", "")):
            self.send_response(401)
            self.end_headers()
            return
        event = json.loads(body)
        with sqlite3.connect(database) as conn:
            conn.execute("INSERT OR IGNORE INTO activations(resource) VALUES (?)", (event["resource"],))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"accepted"}')

signal.signal(signal.SIGTERM, lambda *args:sys.exit(0))
HTTPServer(("127.0.0.1", int(os.environ["DURESS_PORT"])), Handler).serve_forever()
'''


def initialize(directory: Path, project: str) -> Path:
    Project(project=project)
    files = {"app.py":STARTER_APP,
        "events.json":json.dumps({"events":{"activate":{"payload":{"resource":"resource-a"}}}},indent=2)+"\n",
        "contract.json":json.dumps({"schema_version":1,"id":"resource-activation-v1","version":1,
            "entities":{"activations":{"table":"activations","key":"id","columns":["id","resource"]}},
            "invariants":[{"id":"one-activation-per-resource","kind":"unique_by","entity":"activations","keys":["resource"]},
                          {"id":"requested-activation-exists","kind":"expected_effect","entity":"activations"}]},indent=2)+"\n",
        "duress.toml":f'''[duress]
schema_version = 1
project = "{project}"

[target]
id = "{project}-local"
mode = "managed"
cwd = "."
init = ["python", "app.py", "init"]
serve = ["python", "app.py", "serve"]

[delivery]
endpoint = "/webhooks"
signature = "hmac-sha256"

[observation]
adapter = "sqlite@1"

[contract]
file = "contract.json"

[fixtures]
file = "events.json"

[[scenarios]]
id = "duplicate-activation"
deliveries = [{{id="first", fixture="activate", event_id="activation-a"}}, {{id="duplicate", fixture="activate", event_id="activation-a"}}]
steps = [{{deliveries=["first"]}}, {{deliveries=["duplicate"]}}]
expected_status = {{first="2xx", duplicate="2xx"}}
[scenarios.context]
expected_effects = [{{entity="activations", match={{resource="resource-a"}}, min_delta=1, max_delta=1}}]
'''}
    directory = directory.expanduser().absolute()
    for name in files:
        if (directory/name).exists() or (directory/name).is_symlink():
            raise ValueError(f"refusing to overwrite {directory/name}")
    directory.mkdir(parents=True,exist_ok=True)
    for name, content in files.items():
        with (directory/name).open("x") as handle:
            handle.write(content)
    return directory/"duress.toml"
