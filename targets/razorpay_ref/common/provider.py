"""Client for the mock payment provider (the controlled dependency)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class ProviderError(RuntimeError):
    """The provider answered with an unexpected status or could not be reached."""


@dataclass(frozen=True)
class ProviderPayment:
    id: str
    order_id: str
    amount: int
    currency: str
    status: str

    @classmethod
    def from_json(cls, payload: dict) -> "ProviderPayment":
        try:
            return cls(
                id=payload["id"],
                order_id=payload["order_id"],
                amount=int(payload["amount"]),
                currency=payload["currency"],
                status=payload["status"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"malformed provider payment: {exc}") from exc


async def fetch_payment(
    base_url: str,
    payment_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProviderPayment | None:
    """Return the provider's payment, or ``None`` for an unknown id.

    The URL includes the per-trial namespace prefix, e.g.
    ``http://127.0.0.1:PORT/t/<trial_id>``.
    """
    url = f"{base_url.rstrip('/')}/v1/payments/{payment_id}"
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise ProviderError(str(exc)) from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ProviderError(f"provider returned {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("provider returned non-JSON") from exc
    return ProviderPayment.from_json(payload)
