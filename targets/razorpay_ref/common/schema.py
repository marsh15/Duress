"""SQLite schema for the reference integration.

Both variants share the same tables. The fixed variant adds the two UNIQUE
constraints on ``fulfilments`` that make single fulfilment a database-level
guarantee rather than an application-level convention. The vulnerable variant
intentionally omits them (M0 defect D-1).
"""

VULNERABLE_SCHEMA = """
CREATE TABLE orders (
    id       TEXT PRIMARY KEY,
    amount   INTEGER NOT NULL CHECK (amount > 0),
    currency TEXT    NOT NULL,
    status   TEXT    NOT NULL CHECK (status IN ('created', 'fulfilled'))
);

CREATE TABLE payments (
    id       TEXT PRIMARY KEY,
    order_id TEXT    NOT NULL REFERENCES orders(id),
    amount   INTEGER NOT NULL CHECK (amount > 0),
    currency TEXT    NOT NULL,
    status   TEXT    NOT NULL CHECK (status IN ('created', 'authorized', 'captured', 'failed'))
);

CREATE TABLE fulfilments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   TEXT    NOT NULL REFERENCES orders(id),
    payment_id TEXT    NOT NULL REFERENCES payments(id),
    amount     INTEGER NOT NULL CHECK (amount > 0),
    created_at TEXT    NOT NULL
);

CREATE INDEX idx_fulfilments_order   ON fulfilments(order_id);
CREATE INDEX idx_fulfilments_payment ON fulfilments(payment_id);
"""

FIXED_SCHEMA = """
CREATE TABLE orders (
    id       TEXT PRIMARY KEY,
    amount   INTEGER NOT NULL CHECK (amount > 0),
    currency TEXT    NOT NULL,
    status   TEXT    NOT NULL CHECK (status IN ('created', 'fulfilled'))
);

CREATE TABLE payments (
    id       TEXT PRIMARY KEY,
    order_id TEXT    NOT NULL REFERENCES orders(id),
    amount   INTEGER NOT NULL CHECK (amount > 0),
    currency TEXT    NOT NULL,
    status   TEXT    NOT NULL CHECK (status IN ('created', 'authorized', 'captured', 'failed'))
);

CREATE TABLE fulfilments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   TEXT    NOT NULL REFERENCES orders(id),
    payment_id TEXT    NOT NULL REFERENCES payments(id),
    amount     INTEGER NOT NULL CHECK (amount > 0),
    created_at TEXT    NOT NULL,
    UNIQUE (order_id),
    UNIQUE (payment_id)
);

CREATE INDEX idx_fulfilments_order   ON fulfilments(order_id);
CREATE INDEX idx_fulfilments_payment ON fulfilments(payment_id);
"""

SCHEMAS = {"vulnerable": VULNERABLE_SCHEMA, "fixed": FIXED_SCHEMA}
VARIANTS = tuple(SCHEMAS)


def schema_for(variant: str) -> str:
    try:
        return SCHEMAS[variant]
    except KeyError:
        raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}") from None
