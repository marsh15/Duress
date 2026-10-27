# Productization execution evidence

AI-assisted implementation started from verified `4684bd1743c0b09104a53750230b5e98fae2cb2f`; local and fetched remote main agreed. Historical evidence is unchanged. Commands below ran locally on macOS arm64, Python 3.12.14, PostgreSQL 14.21 unless noted.

## Adapter and invariant foundation

- `uv run --python 3.12 pytest -q tests/unit/test_external_manifest.py tests/unit/test_exec_semantics.py`: **16 passed**, including unchanged dispatcher semantics.
- `uv run --python 3.12 pytest -q tests/unit/test_external_manifest.py tests/unit/test_external_delivery.py tests/unit/test_external_contract.py tests/unit/test_external_observers.py`: **25 passed** at that point. Observer PostgreSQL unit coverage uses a fake driver and is not live database evidence.
- `uv run --python 3.12 pytest -q tests/integration/test_external.py tests/unit/test_external_manifest.py tests/unit/test_external_delivery.py`: **7 passed**. A Python HTTP application created outside the source package in a temporary directory, initialized SQLite, accepted actual HTTP delivery, and independently exposed one settled persisted effect. Missing required tables returned INCONCLUSIVE. No application logs or unscoped database files were retained.
- Development corrections: the initial test command referenced nonexistent `test_runner.py` (no tests ran); corrected to `test_exec_semantics.py`. The missing-table test exposed a partial-baseline acceptance gap; baseline now requires all configured entities before delivery.
- Docker CLI exists but daemon was initially unavailable. Local PostgreSQL is reachable. GitHub authentication is available. These facts do not establish deployment or remote CI success.
