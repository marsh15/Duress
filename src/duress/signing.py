"""Raw-body HMAC-SHA256 signing for controlled deliveries."""

from __future__ import annotations

import hashlib
import hmac

HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def invalid_signature(secret: str, delivery_id: str, body: bytes) -> str:
    """exec@1 clause 7: a signature that cannot verify.

    The HMAC key is the digest of ``duress-invalid:<delivery_id>``. The real
    secret is deliberately not used, so no secret material ends up in an
    artifact and the target can never accept the result.
    """
    del secret  # kept in the signature for call-site symmetry
    key = hashlib.sha256(f"duress-invalid:{delivery_id}".encode()).hexdigest()
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign(secret, body), signature)