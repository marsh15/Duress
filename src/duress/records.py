"""Read-only investigation records and presentation metadata for API operations.

This module neither schedules experiments nor interprets execution outcomes.
The engine services remain responsible for every verdict.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .store import atomic_write_json

_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def read_record(directory: Path, record_id: str) -> dict[str, Any]:
    if not _ID.fullmatch(record_id):
        raise FileNotFoundError(record_id)
    path = directory / f"{record_id}.json"
    if path.is_symlink():
        raise FileNotFoundError(record_id)
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        raise FileNotFoundError(record_id) from None
    if not isinstance(payload, dict):
        raise FileNotFoundError(record_id)
    return payload


def ensure_showcase(workspace) -> None:
    """Install only bundled, previously recorded artifacts into an empty store."""
    for path in (workspace.root / "showcase" / "dna").glob("*.json"):
        destination = workspace.home / "dna" / path.name
        if not destination.exists():
            payload = read_record(path.parent, path.stem)
            atomic_write_json(destination, payload)


def load_investigation_record(workspace, investigation_id: str) -> dict[str, Any]:
    directory = workspace.home / "investigations"
    local = directory / f"{investigation_id}.json"
    if local.exists() or local.is_symlink():
        return read_record(directory, investigation_id)
    showcase = read_record(workspace.root / "showcase", "investigation")
    if investigation_id not in {"showcase", showcase.get("investigation_id")}:
        raise FileNotFoundError(investigation_id)
    return showcase


def investigation_index(workspace) -> list[dict[str, Any]]:
    items = []
    for path in (workspace.home / "investigations").glob("*.json"):
        try:
            record = read_record(path.parent, path.stem)
            if record.get("investigation_id") == path.stem:
                items.append(record)
        except FileNotFoundError:
            continue
    try:
        showcase = read_record(workspace.root / "showcase", "investigation")
        if all(item["investigation_id"] != showcase.get("investigation_id") for item in items):
            items.append(showcase)
    except FileNotFoundError:
        pass
    return sorted(items, key=lambda item: str(item.get("created_at", "")), reverse=True)


def save_operation_record(workspace, *, kind: str, payload: dict[str, Any], **context) -> str:
    operation_id = uuid4().hex[:16]
    record = {"operation_id": operation_id, "kind": kind,
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "payload": payload, **context}
    atomic_write_json(workspace.home / "operations" / f"{operation_id}.json", record)
    return operation_id


def load_operation_record(workspace, operation_id: str) -> dict[str, Any]:
    return read_record(workspace.home / "operations", operation_id)


def operation_index(workspace) -> list[dict[str, Any]]:
    items = []
    for path in (workspace.home / "operations").glob("*.json"):
        try:
            items.append(read_record(path.parent, path.stem))
        except FileNotFoundError:
            continue
    return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)
