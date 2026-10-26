from __future__ import annotations

import json
from pathlib import Path

import uvicorn

from duress import api, cli


ROOT = Path(__file__).resolve().parents[2]


def test_container_excludes_local_recovery_archives_and_runtime_evidence():
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".recovery/" in ignored
    assert ".duress/" in ignored


def test_container_entrypoint_hands_signals_to_the_single_server_process():
    dockerfile = (ROOT / "Dockerfile").read_text()
    command_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    command = json.loads(command_line.removeprefix("CMD "))

    assert command == [
        "sh",
        "-c",
        "exec duress serve --host 0.0.0.0 --port ${PORT:-8000}",
    ]
    assert "--workers" not in command[-1]


def test_render_notes_full_ui_and_live_provider_settings():
    render_config = (ROOT / "render.yaml").read_text()

    assert "The full investigation UI is enabled." in render_config
    assert "DURESS_LLM_BASE_URL" in render_config
    assert "DURESS_LLM_MODEL" in render_config
    assert "DURESS_LLM_API_KEY" in render_config
    assert "browses recorded evidence" not in render_config
    assert "model-provider configuration is pending" not in render_config


def test_local_server_defaults_to_loopback_and_one_worker(monkeypatch):
    observed: dict[str, object] = {}
    monkeypatch.setattr(api, "create_app", lambda: object())
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda _app, **options: observed.update(options),
    )

    cli.serve_command(host="127.0.0.1", port=8000)

    assert observed == {"host": "127.0.0.1", "port": 8000, "log_level": "info"}
    assert uvicorn.Config(object(), **observed).workers == 1
