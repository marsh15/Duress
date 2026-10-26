"""Shared M1/M2 fixtures: one provider stub and one target registry per session."""

from __future__ import annotations

from pathlib import Path

import pytest

from duress.profiles import ProfileRegistry
from duress.provider_stub import StubServer
from duress.store import ensure_dirs


@pytest.fixture(scope="session")
def registry() -> ProfileRegistry:
    return ProfileRegistry.discover(Path(__file__).resolve().parents[1])


@pytest.fixture(scope="session")
def stub() -> StubServer:
    server = StubServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / ".duress"
    ensure_dirs(path)
    return path