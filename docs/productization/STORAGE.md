# Local project storage

The external integration store keeps records for separate local projects under
one storage root. It is implemented by `duress.external.storage` and does not
depend on the CLI or manifest loader.

## Paths and project ids

The storage root is `DURESS_HOME` when set, otherwise
`~/.local/share/duress`. `ProjectStore(project, home=...)` accepts the storage
root as `home`; its `.home` property is the absolute project directory:

```text
<storage-root>/
  registry.json
  projects/<project>/
    investigations/<record-id>.json
    dna/<record-id>.json
    operations/<record-id>.json
```

Project ids are 1–63 lowercase ASCII letters, digits, underscores or hyphens,
starting with a letter or digit. Record kinds are exactly `investigations`,
`dna` and `operations`. Record ids are 1–128 ASCII letters, digits, dots,
underscores or hyphens; `.` and `..` are rejected. Managed directories and
record files may not be symlinks. Directories are private to the local user
where the platform supports POSIX permissions, and record files are written
with mode `0600`.

## Record format, version 1

Each record is a UTF-8 JSON object with this envelope:

```json
{
  "schema_version": 1,
  "project": "ticket-service",
  "kind": "investigations",
  "record_id": "run-01",
  "created_at": "2026-10-27T09:30:00.000000+00:00",
  "updated_at": "2026-10-27T09:30:00.000000+00:00",
  "status": "complete",
  "owner": {"pid": 1234, "start_time": "platform process identity"},
  "payload": {"status": "complete", "result": "PASS"},
  "sha256": "digest of the canonical envelope without this field"
}
```

`status` is copied from `payload.status`, or `payload.state`, when that value is
a string. It may be `null`. `owner` records the saving process id and the
platform process start identity. The digest is SHA-256 over compact,
UTF-8, sorted-key JSON with non-finite numbers rejected. It detects accidental
or unauthorized edits; it is not a signature and does not prove who created a
record.

Records are written to a unique same-directory temporary file, flushed, then
renamed into place. Concurrent writers cannot expose partial JSON. Concurrent
updates to one id are atomic and last-writer-wins. `list(kind)` returns
integrity-checked records newest-created-first and skips corrupt or partial
files; `load(kind, id)` raises `CorruptRecordError` for an invalid record.

## API

```python
from pathlib import Path
from duress.external.storage import ProjectStore

store = ProjectStore("ticket-service")
record_path = store.save("investigations", {"status": "running"})
records = store.list("investigations")
record = store.load("investigations", records[0]["record_id"])
diagnostics = store.diagnostics()
recovery = store.reconcile()
preview = store.cleanup(retain_days=30)  # dry-run is the default
exported_path = store.export_to(Path("ticket-service.json"))
```

`diagnostics()` returns project paths, valid counts by kind, invalid record
paths and detected symlinks. It never returns record payloads. `cleanup()` uses
`updated_at`, removes only valid old records, and previews by default; pass
`dry_run=False` to delete. Invalid records and symlinks are reported as
skipped. There is no implicit cleanup on startup.

`reconcile()` examines only records whose status is `running`. It changes a
record to `interrupted` only when the recorded PID is gone or its process
start-time differs, which also detects PID reuse. A matching live process is
left alone. If process identity cannot be established, the record is reported
under `uncertain` and left untouched. Reconciliation does not claim to stop or
settle a process.

## Export and import

`export_records(store, destination)` and `store.export_to(destination)` write a
version-1 JSON bundle to the explicit destination file. The bundle contains a
digest for every record and a digest over the full bundle. The project registry
and local manifest paths are not exported.

`import_records(source, destination)` and
`store.import_from(source, destination)` take an explicit destination storage
root and import into `destination/projects/<bundle-project>`. They verify the
bundle digest, every record digest, project, kind, id and schema before writing
anything. Identical existing records are kept. An id collision with different
content fails instead of overwriting local data. Reimporting an unchanged
bundle is safe.

Exports are local JSON files, not encrypted archives. Store only data you are
permitted to export. The store redacts values under keys naming secrets,
passwords, tokens, credentials, DSNs, authorization, connection URLs/strings
and common config references, and masks private-key blocks, bearer values,
URL credentials and sensitive query parameters in free text. Callers must pass secret references instead of live
credentials and should not embed secrets in arbitrary text fields; no generic
JSON store can identify every application-specific secret format.

## Project registration

`register_project(config, home=None)` reads a local TOML manifest and requires
`[duress].project`. `duress.schema_version` defaults to `1` and any supplied
value must be `1`. The registry at `<storage-root>/registry.json` contains only
the project id, canonical absolute manifest path and registration timestamps;
it does not copy manifest content. A process lock plus atomic replacement
prevents concurrent registrations from losing entries.

`registered_projects(home=None)` returns a sorted list of those safe registry
fields. It does not open manifests or resolve secrets. Both functions use the
same root selection as `ProjectStore`.

## Scope

This is local filesystem persistence for a developer workstation. It provides
project separation, atomic writes, checksums and conservative recovery; it is
not multi-user authorization, encryption at rest, a signed evidence format or
a distributed job database.
