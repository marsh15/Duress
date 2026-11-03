"""Bounded remote-quiescence probe for staging targets.

Quiescence is established only when all of these hold inside ``timeout_ms``:

* the owned application attests, with a fresh nonce echoed back, that it has
  no in-flight writes (exact body ``{"quiescent": true, "nonce": N}``);
* the observed state is available, valid and identical across ``stable_reads``
  consecutive reads; and
* every such read is bracketed by a successful attestation before and after.

HTTP status codes, delivery responses and an unchanged state alone never count.
A missing endpoint, an unreachable host, a replayed nonce or a changing state
all leave the outcome unestablished, so the verdict stays INCONCLUSIVE.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..models import canonical_sha256
from .contract import Snapshot
from .manifest import LoadedProject
from .network import transport

MAX_ATTESTATION_BYTES = 4096
UNAVAILABLE_STATUSES = {404, 405, 501}


@dataclass
class QuiescenceOutcome:
    established: bool
    reason: str | None = None
    snapshot: Snapshot | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


async def _attest(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> tuple[bool, str]:
    nonce = secrets.token_hex(16)
    try:
        response = await client.get(url, params={"nonce": nonce}, headers=headers)
    except httpx.HTTPError:
        return False, "quiescence_unreachable"
    if response.status_code in UNAVAILABLE_STATUSES:
        return False, "quiescence_probe_unavailable"
    if response.status_code != 200 or len(response.content) > MAX_ATTESTATION_BYTES:
        return False, "quiescence_not_attested"
    try:
        body = response.json()
    except ValueError:
        return False, "quiescence_not_attested"
    # Strict shape check: exactly two keys, a real boolean and the echoed nonce.
    if (isinstance(body, dict) and set(body) == {"quiescent", "nonce"}
            and body["quiescent"] is True and body["nonce"] == nonce):
        return True, ""
    return False, "quiescence_not_attested"


async def await_quiescence(project: LoadedProject, read_state: Callable[[], Snapshot],
                           headers: dict[str, str]) -> QuiescenceOutcome:
    """Run the settle probe. ``read_state`` is a blocking settled observation."""
    probe = project.manifest.target.quiescence
    if probe is None:
        return QuiescenceOutcome(False, "staging_quiescence_unverified")
    url = project.manifest.target.base_url.rstrip("/") + probe.path
    interval = probe.interval_ms / 1000
    deadline = time.monotonic() + probe.timeout_ms / 1000
    counts = {"attestations": 0, "reads": 0, "stable": 0}
    reason = "quiescence_timeout"
    previous: str | None = None
    last_snapshot: Snapshot | None = None
    async with httpx.AsyncClient(transport=transport(project), follow_redirects=False,
                                 trust_env=False, timeout=httpx.Timeout(min(2.0, probe.timeout_ms / 1000))) as client:
        while time.monotonic() < deadline:
            ok, why = await _attest(client, url, headers)
            if ok:
                counts["attestations"] += 1
            else:
                if why == "quiescence_probe_unavailable":
                    return QuiescenceOutcome(False, why, evidence=_evidence(probe, counts, why))
                reason, previous = why, None
                await asyncio.sleep(interval)
                continue
            snapshot = await asyncio.to_thread(read_state)
            counts["reads"] += 1
            if not (snapshot.available and snapshot.valid):
                reason, previous = "quiescence_state_unavailable", None
                await asyncio.sleep(interval)
                continue
            # The read is only trusted when the attestation holds on both sides of it.
            ok, why = await _attest(client, url, headers)
            if not ok:
                reason, previous = why, None
                await asyncio.sleep(interval)
                continue
            counts["attestations"] += 1
            current = canonical_sha256(snapshot.entities)
            if current == previous:
                counts["stable"] += 1
            else:
                counts["stable"] = 1
                previous = current
                last_snapshot = snapshot
                reason = "quiescence_state_unstable"
            if counts["stable"] >= probe.stable_reads:
                return QuiescenceOutcome(True, None, snapshot,
                                         _evidence(probe, counts, "established"))
            await asyncio.sleep(interval)
    return QuiescenceOutcome(False, reason, last_snapshot, _evidence(probe, counts, reason))


def _evidence(probe, counts: dict[str, int], outcome: str) -> dict[str, Any]:
    # Counts and reasons only: no nonce, header value or body is recorded.
    return {"probe": "cooperative_attestation", "outcome": outcome,
            "attestations": counts["attestations"], "reads": counts["reads"],
            "stable_reads_required": probe.stable_reads, "interval_ms": probe.interval_ms,
            "timeout_ms": probe.timeout_ms}
