"""M0 harness fixtures: one provider stub per session, one temp root per test."""

from __future__ import annotations

import pytest

from .stub import StubServer


@pytest.fixture(scope="session")
def stub():
    server = StubServer().start()
    try:
        yield server
    finally:
        server.stop()
