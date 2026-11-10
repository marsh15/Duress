"""Opt-in staging proxy and CA bundle. Defaults stay direct with default trust."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from duress.external import network
from duress.external.manifest import Target, load_project
from duress.external.quiescence import await_quiescence
from tests.unit.staging_support import (
    QUIESCENCE_TABLE,
    FakeStaging,
    ConnectProxy,
    ForwardProxy,
    load_staging,
    make_self_signed,
    write_manifest,
)
from duress.external.contract import Snapshot


def reader():
    return lambda: Snapshot(entities={"effects": [{"id": 1, "entity": "owned"}]},
                            available=True, valid=True, settled=True)


def test_proxy_and_ca_and_quiescence_are_refused_for_managed_targets():
    managed = dict(id="local", init=["python", "init.py"], serve=["python", "app.py"])
    for extra in ({"proxy": "http://proxy.test:3128"}, {"proxy_ref": "PROXY_URL"},
                  {"ca_bundle": "ca.pem"}, {"quiescence": {}}):
        with pytest.raises(ValidationError):
            Target(**managed, **extra)


def test_staging_proxy_must_be_an_origin_without_credentials(tmp_path):
    base = "http://127.0.0.1:9"
    for proxy in ("http://user:hunter2@proxy.test:3128", "ftp://proxy.test", "http://proxy.test/path"):
        path = write_manifest(tmp_path / "bad", base, target_extra=f'proxy = "{proxy}"')
        with pytest.raises(ValueError) as caught:
            load_project(path)
        assert "hunter2" not in str(caught.value)


def test_proxy_and_proxy_ref_are_mutually_exclusive(tmp_path):
    path = write_manifest(tmp_path, "http://127.0.0.1:9",
                          target_extra='proxy = "http://proxy.test:3128"\nproxy_ref = "PROXY_URL"')
    with pytest.raises(ValueError):
        load_project(path)


def test_quiescence_path_and_stable_window_are_bounded(tmp_path):
    with pytest.raises(ValidationError):
        Target(id="staging", mode="staging", base_url="http://127.0.0.1:9", allowed_origins=["http://127.0.0.1:9"],
               staging_authorized=True, quiescence={"path": "/q?x=1"})
    with pytest.raises(ValidationError):
        Target(id="staging", mode="staging", base_url="http://127.0.0.1:9", allowed_origins=["http://127.0.0.1:9"],
               staging_authorized=True, quiescence={"stable_reads": 10, "interval_ms": 1000, "timeout_ms": 2000})


def test_missing_or_unparseable_ca_bundle_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="existing file"):
        load_project(write_manifest(tmp_path / "missing", "http://127.0.0.1:9",
                                    target_extra='ca_bundle = "absent.pem"'))
    project = load_project(write_manifest(tmp_path / "garbage", "http://127.0.0.1:9",
                                          target_extra='ca_bundle = "ca.pem"',
                                          files={"ca.pem": b"not a certificate\n"}))
    with pytest.raises(ValueError, match="PEM"):
        network.validate(project)


@pytest.mark.asyncio
async def test_default_is_direct_and_ambient_proxy_environment_is_ignored(tmp_path, monkeypatch):
    with ForwardProxy() as decoy, FakeStaging("honest") as app:
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, decoy.origin)
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        assert network.transport(project) is None
        outcome = await await_quiescence(project, reader(), {})
    assert outcome.established
    assert decoy.requests == [], "ambient proxy environment must not be used"


@pytest.mark.asyncio
async def test_declared_proxy_carries_the_probe(tmp_path):
    with ForwardProxy() as proxy, FakeStaging("honest") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE,
                               target_extra=f'proxy = "{proxy.origin}"')
        outcome = await await_quiescence(project, reader(), {})
    assert outcome.established
    assert any(item.startswith(app.origin + "/duress/quiescence?nonce=") for item in proxy.requests)


@pytest.mark.asyncio
async def test_proxy_ref_resolves_from_environment_and_bad_values_do_not_echo(tmp_path, monkeypatch):
    with ForwardProxy() as proxy, FakeStaging("honest") as app:
        monkeypatch.setenv("DURESS_TEST_PROXY", proxy.origin)
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE,
                               target_extra='proxy_ref = "DURESS_TEST_PROXY"')
        outcome = await await_quiescence(project, reader(), {})
    assert outcome.established and proxy.requests

    monkeypatch.setenv("DURESS_TEST_PROXY", "ftp://user:topsecret@proxy.test")
    project = load_staging(tmp_path / "bad", app.origin, quiescence=QUIESCENCE_TABLE,
                           target_extra='proxy_ref = "DURESS_TEST_PROXY"')
    with pytest.raises(ValueError) as caught:
        network.validate(project)
    assert "topsecret" not in str(caught.value)


@pytest.mark.asyncio
async def test_default_trust_rejects_self_signed_staging_and_declared_bundle_accepts(tmp_path):
    material = make_self_signed(tmp_path / "certs")
    if material is None:
        pytest.skip("openssl CLI is not available to create a localhost certificate")
    cert, key = material
    with FakeStaging("honest", tls=(str(cert), str(key))) as app:
        direct = load_staging(tmp_path / "direct", app.origin, quiescence=QUIESCENCE_TABLE)
        untrusted = await await_quiescence(direct, reader(), {})
        trusted_project = load_staging(tmp_path / "trusted", app.origin, quiescence=QUIESCENCE_TABLE,
                                       target_extra='ca_bundle = "ca.pem"',
                                       files={"ca.pem": cert.read_bytes()})
        trusted = await await_quiescence(trusted_project, reader(), {})
    assert not untrusted.established and untrusted.reason == "quiescence_unreachable"
    assert trusted.established


@pytest.mark.asyncio
async def test_https_through_a_connect_proxy_with_a_declared_ca_bundle(tmp_path):
    material = make_self_signed(tmp_path / "certs")
    if material is None:
        pytest.skip("openssl CLI is not available to create a localhost certificate")
    cert, key = material
    with ConnectProxy() as proxy, FakeStaging("honest", tls=(str(cert), str(key))) as app:
        project = load_staging(tmp_path / "project", app.origin, quiescence=QUIESCENCE_TABLE,
                               target_extra=f'proxy = "{proxy.origin}"\nca_bundle = "ca.pem"',
                               files={"ca.pem": cert.read_bytes()})
        outcome = await await_quiescence(project, reader(), {})
    assert outcome.established, outcome
    assert proxy.tunnels, "the TLS connection must have been tunnelled through CONNECT"
    assert set(proxy.tunnels) == {f"127.0.0.1:{app.port}"}


@pytest.mark.asyncio
async def test_connect_tunnel_does_not_bypass_certificate_verification(tmp_path):
    material = make_self_signed(tmp_path / "certs")
    if material is None:
        pytest.skip("openssl CLI is not available to create a localhost certificate")
    cert, key = material
    with ConnectProxy() as proxy, FakeStaging("honest", tls=(str(cert), str(key))) as app:
        project = load_staging(tmp_path / "project", app.origin, quiescence=QUIESCENCE_TABLE,
                               target_extra=f'proxy = "{proxy.origin}"')
        outcome = await await_quiescence(project, reader(), {})
    assert not outcome.established and outcome.reason == "quiescence_unreachable"
    assert proxy.tunnels, "the handshake was attempted through the tunnel and then refused"
    assert app.probes == 0


@pytest.mark.asyncio
async def test_unreachable_proxy_is_inconclusive_not_a_pass(tmp_path):
    dead = ConnectProxy()
    origin = dead.origin
    dead.server.server_close()  # nothing listens on that port any more
    with FakeStaging("honest") as app:
        project = load_staging(tmp_path / "project", app.origin, quiescence=QUIESCENCE_TABLE,
                               target_extra=f'proxy = "{origin}"')
        outcome = await await_quiescence(project, reader(), {})
    assert not outcome.established and outcome.reason == "quiescence_unreachable"
    assert app.probes == 0


@pytest.mark.asyncio
async def test_proxy_that_cannot_reach_the_origin_is_inconclusive_not_a_pass(tmp_path):
    material = make_self_signed(tmp_path / "certs")
    if material is None:
        pytest.skip("openssl CLI is not available to create a localhost certificate")
    cert, key = material
    with FakeStaging("honest", tls=(str(cert), str(key))) as app:
        origin = app.origin
    # The application has stopped: the proxy's CONNECT gets 502 for its address.
    with ConnectProxy() as proxy:
        project = load_staging(tmp_path / "project", origin, quiescence=QUIESCENCE_TABLE,
                               target_extra=f'proxy = "{proxy.origin}"\nca_bundle = "ca.pem"',
                               files={"ca.pem": cert.read_bytes()})
        outcome = await await_quiescence(project, reader(), {})
    assert proxy.tunnels
    assert not outcome.established and outcome.reason == "quiescence_unreachable"
