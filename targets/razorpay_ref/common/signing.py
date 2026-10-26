"""Raw-body HMAC-SHA256 signing, matching the reference provider scheme."""

from __future__ import annotations

import hashlib
import hmac

HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = sign(secret, body)
    return hmac.compare_digest(expected, signature)


def invalid_signature_for(secret: str, delivery_id: str) -> str:
    """The exec@1 rule for a deliberately invalid signature (test-side only)."""
    derived = hashlib.sha256(f"duress-invalid:{delivery_id}".encode()).hexdigest()
    return sign(secret, derived.encode())
