"""Shared M1/M2 fixtures: one provider stub and one target registry per session."""

from __future__ import annotations

from pathlib import Path

import pytest

from duress.profiles import ProfileRegistry, load_seed
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

@pytest.fixture(scope="session")
def dna_artifact(registry, stub, tmp_path_factory):
    """One real DNA, characterised once and reused by the M2 acceptance tests."""
    import asyncio

    from duress import dna as dna_module
    from duress.scenarios import build_scenario

    profile = registry.get("razorpay-vulnerable")
    seed = load_seed(profile)
    scenario = build_scenario(
        "S3", {"copies": 2, "fault": "rendezvous", "latency_ms": 200}, profile.roles, seed
    )
    workdir = tmp_path_factory.mktemp("dna-build")
    home = workdir / ".duress"
    ensure_dirs(home)
    with StubServer() as server:
        document, characterisation = asyncio.run(
            dna_module.build_dna(
                profile,
                scenario,
                server,
                workdir_root=workdir,
                home=home,
            )
        )
    return document, characterisation
