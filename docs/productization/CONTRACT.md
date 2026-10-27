# External contracts (external@1)

duress.external.contract supplies strict Pydantic models and a declarative
evaluator for independently observed business state. Contracts are TOML or
JSON data. They contain no Python, SQL, or other executable expressions.

## Shape and validation

The root fields are schema_version=1, a stable lower-case id, version=1, an
entities mapping, and a non-empty invariants list. Every entity has a simple
SQL table, one identity key, and an allowlist of columns containing that key.
Entity names are lower-case snake case. Table and column names accept only
simple identifier characters. Tables must be distinct. Unknown fields,
versions, kinds, entity names, and column references are rejected when parsed.

Example:

    schema_version = 1
    id = "ticketing-v1"
    version = 1

    [entities.bookings]
    table = "booking"
    key = "id"
    columns = ["id", "status", "amount", "capacity"]

    [entities.tickets]
    table = "ticket"
    key = "id"
    columns = ["id", "booking_id", "amount"]

    [[invariants]]
    id = "one-ticket-per-booking"
    kind = "unique_by"
    entity = "tickets"
    keys = ["booking_id"]
    max_count = 1

    [[invariants]]
    id = "ticket-has-booking"
    kind = "reference_exists"
    entity = "tickets"
    reference = "booking_id"
    target_entity = "bookings"
    target_key = "id"

    [[invariants]]
    id = "ticket-issued"
    kind = "expected_effect"
    entity = "tickets"

    [[invariants]]
    id = "booking-transition"
    kind = "state_transition"
    entity = "bookings"
    field = "status"
    allowed = [{ from_state = "pending", to_state = "confirmed" }]

    [[invariants]]
    id = "ticket-amount"
    kind = "value_consistency"
    mode = "fields_equal"
    entity = "tickets"
    field = "amount"
    related_entity = "bookings"
    reference = "booking_id"
    related_key = "id"
    related_field = "amount"

    [[invariants]]
    id = "booking-capacity"
    kind = "value_consistency"
    mode = "count_at_most"
    entity = "bookings"
    field = "capacity"
    related_entity = "tickets"
    reference = "booking_id"
    related_key = "id"

    [[invariants]]
    id = "booking-scope"
    kind = "scope_ownership"
    entity = "bookings"

    [[invariants]]
    id = "ack-matches-event"
    kind = "acknowledgement_consistency"

## Invariants

Each invariant has a unique stable id, version=1, and discriminating kind.

- unique_by: group final rows by keys; fail above max_count (default 1).
- reference_exists: each non-null entity.reference must match a declared identity
  in target_entity.target_key. The target key must be its declared key.
- expected_effect: scenario context supplies selectors and acceptable
  before/after count deltas.
- state_transition: compare field across snapshots, reject transitions not
  listed in allowed, and optionally require scenario-specific transitions.
- value_consistency with mode=fields_equal: join through reference and
  related_key, then compare field and related_field.
- value_consistency with mode=count_at_most: entity is the parent; count related
  rows whose reference equals its related_key and bound that count by the
  parent's numeric field. This models capacities and quotas.
- scope_ownership: compare before and after rows; only context-listed identities
  may be added, removed, or changed.
- acknowledgement_consistency: require a successful response to match the
  declared event intent and observed effect. A duplicate acknowledgement is
  valid when the prior effect exists and no extra matching row was added.

## Constrained scenario context

evaluate(contract, before, after, *, context={}) accepts expected_effects,
required_transitions, scopes, and acknowledgement. Unknown keys and undeclared
references make results invalid.

    context = {
        "expected_effects": [{
            "entity": "tickets",
            "match": {"booking_id": "b-42"},
            "min_delta": 1,
            "max_delta": 1,
        }],
        "required_transitions": [{
            "entity": "bookings",
            "identity": "b-42",
            "from_state": "pending",
            "to_state": "confirmed",
        }],
        "scopes": [{"entity": "bookings", "identities": ["b-42"]}],
        "acknowledgement": {
            "successful": True,
            "status_code": 200,
            "intent": "create",
            "entity": "tickets",
            "match": {"booking_id": "b-42"},
        },
    }

Expected-effect deltas are after_count - before_count. For an expected duplicate
with no new row, use min_delta=0, max_delta=0. Transition identity uses the
entity's declared key. scopes may contain an empty identity list to allow no
changes. Acknowledgement intents are create, update, delete, duplicate, and
no_effect. Successful status evidence must contain a 2xx status and an
entity/match selector. HTTP success alone never proves a business effect.

## API and evidence status

load_contract(path) accepts .toml and .json. Snapshot contains entities,
available, valid, settled, provenance, and error. evaluate returns one
InvariantResult per invariant in contract order. Each result contains id,
version, status, reason, identities, and explanation. Status is satisfied,
violated, not_evaluable, or invalid.

Missing tables, columns, rows needed for a comparison, unavailable evidence,
missing scenario expectations, and an unsettled final snapshot yield
not_evaluable, never an empty-state pass. Malformed snapshots, undeclared
fields, incompatible values, duplicate identities needed for a comparison, and
malformed context yield invalid. Put adapter/version, schema fingerprint, and
observation time in provenance. Never place a DSN, credential, or raw driver
exception in error.

## SQLite and PostgreSQL observers

duress.external.observers.observe(contract, *, kind, database, schema="public",
settled=False) supports kind=sqlite for a SQLite file and kind=postgresql for a
resolved PostgreSQL DSN. SQLite opens in read-only URI mode with query_only; its
schema is main (public is accepted as the API default). PostgreSQL uses a
read-only REPEATABLE READ transaction, short connection/statement/lock
timeouts, and a parameterized schema lookup. Install the optional psycopg
dependency for PostgreSQL support.

Both adapters select only contract-listed columns and cap each table at 5,000
rows. Missing tables/columns and over-limit tables are omitted from
Snapshot.entities and identified in provenance, causing dependent checks to be
not_evaluable. Reading a database does not prove an application has settled;
the caller must set settled based on its settlement evidence. PostgreSQL DSNs
and driver error text are not retained. Decimal values become integers or
canonical decimal strings; date/time values become ISO 8601 strings; JSON values
normalize recursively. Unsupported values invalidate a snapshot instead of
being silently dropped.

The format and observer adapter are external@1. A change to interpretation
requires an explicit version change; old contracts are never silently
reinterpreted.

