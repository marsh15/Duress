"""The trust core must still match the hashes pinned in docs/productization/core-freeze.json.

A change to any of these files alters what a verdict means. If one is intentional, say so
in the commit, re-freeze the manifest, and record why in docs/COMPLETION_AUDIT.md or the
release evidence. The wider manifest also pins application code (CLI, API, web); those
hashes are refreshed with the manifest but do not gate this test.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/productization/core-freeze.json"

TRUST_CORE = (
    "src/duress/runner.py",
    "src/duress/oracle.py",
    "src/duress/preflight.py",
    "src/duress/replay.py",
    "src/duress/target.py",
    "src/duress/limits.py",
    "src/duress/aggregation.py",
    "src/duress/expectations.py",
    "src/duress/signing.py",
    "src/duress/invariants/__init__.py",
    "src/duress/invariants/payments_ref_v1.py",
    "src/duress/semantics/__init__.py",
)


def digest(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_trust_core_files_are_pinned_and_unchanged():
    pinned = json.loads(MANIFEST.read_text())["files"]
    missing = [name for name in TRUST_CORE if name not in pinned]
    assert not missing, f"trust-core files missing from the freeze manifest: {missing}"
    drifted = [name for name in TRUST_CORE if digest(name) != pinned[name]]
    assert not drifted, f"trust-core files changed since they were frozen: {drifted}"


def test_every_pinned_file_exists():
    pinned = json.loads(MANIFEST.read_text())["files"]
    assert [name for name in pinned if not (ROOT / name).is_file()] == []
