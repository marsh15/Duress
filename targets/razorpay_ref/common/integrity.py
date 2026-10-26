"""Integrity-error classification (plan P4d / A11).

Only a UNIQUE conflict on ``fulfilments.order_id`` or ``fulfilments.payment_id``
is an intended duplicate. Foreign-key, CHECK, NOT NULL and any other UNIQUE
conflict are real errors and must surface as ``500 integrity_error``.
"""

from __future__ import annotations

import sqlite3

_DUPLICATE_MESSAGES = {
    "UNIQUE constraint failed: fulfilments.order_id": "order",
    "UNIQUE constraint failed: fulfilments.payment_id": "payment",
}

_UNIQUE_ERRONAME = "SQLITE_CONSTRAINT_UNIQUE"


def classify(exc: sqlite3.IntegrityError) -> str | None:
    """Return ``"order"`` / ``"payment"`` for an intended duplicate, else ``None``."""
    if getattr(exc, "sqlite_errorname", None) != _UNIQUE_ERRONAME:
        return None
    return _DUPLICATE_MESSAGES.get(str(exc))
