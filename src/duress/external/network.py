"""Opt-in staging network settings: an explicit proxy and/or CA bundle per project.

The safe default is unchanged: a direct connection with the default certificate
trust store, and no ambient HTTP(S)_PROXY or SSL_CERT_FILE influence. Settings
are honored only for staging targets, which are the only remote destinations.
"""
from __future__ import annotations

import ssl

import httpx

from .manifest import LoadedProject, proxy_error


def proxy_url(project: LoadedProject) -> str | None:
    target = project.manifest.target
    value = project.resolve(target.proxy_ref) if target.proxy_ref else target.proxy
    if value is None:
        return None
    error = proxy_error(value)
    if error:
        # The value itself may carry credentials, so it is never echoed.
        raise ValueError(error)
    return value


def ca_context(project: LoadedProject) -> ssl.SSLContext | None:
    """Trust store made only of the declared bundle. It replaces the default store."""
    if project.ca_bundle_path is None:
        return None
    try:
        return ssl.create_default_context(cafile=str(project.ca_bundle_path))
    except (OSError, ssl.SSLError):
        raise ValueError("ca_bundle could not be loaded as PEM certificates") from None


def validate(project: LoadedProject) -> None:
    """Fail closed before any delivery when opt-in settings are unusable."""
    proxy_url(project)
    ca_context(project)


def transport(project: LoadedProject) -> httpx.AsyncBaseTransport | None:
    """Return None for the default path, or a transport carrying the opt-in settings.

    A fresh transport is returned per call because the client that receives it
    closes it on exit. ``trust_env=False`` keeps ambient environment ignored.
    """
    proxy = proxy_url(project)
    context = ca_context(project)
    if proxy is None and context is None:
        return None
    return httpx.AsyncHTTPTransport(verify=context if context is not None else True,
                                    proxy=proxy, trust_env=False)
