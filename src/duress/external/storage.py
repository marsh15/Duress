"""Project-scoped local records for external integration investigations.

Records are JSON envelopes with an integrity digest, written by atomic rename
into ``{DURESS_HOME}/projects/{project}/{kind}``. The module intentionally
does not depend on the external manifest implementation: registration reads
only the version and project id from TOML.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import time
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..store import process_start_time

SCHEMA_VERSION = 1
KINDS = ("investigations", "dna", "operations")
_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_WORDS = {
    "secret",
    "password",
    "passwd",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "dsn",
    "connectionstring",
    "connectionurl",
    "databaseurl",
    "signingkey",
    "configref",
    "configreference",
    "configpath",
}
_BEARER_VALUE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_PASSWORD = re.compile(
    r"(?i)\b([A-Za-z][A-Za-z0-9+.-]*://[^:/\s@]+:)[^@/\s]+(@)"
)
_QUERY_CREDENTIAL = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password|signature)=)[^&#\s]+"
)


class StorageError(RuntimeError):
    """Base class for local project-store failures."""


class UnsafeStoragePath(StorageError):
    """A managed path is a symlink or is not a regular file/directory."""


class CorruptRecordError(StorageError, ValueError):
    """A record or export is incomplete, unsupported or fails integrity checks."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _validate_project(project: str) -> str:
    if not isinstance(project, str) or not _PROJECT_ID.fullmatch(project):
        raise ValueError(
            "project must be a lowercase id of 1-63 letters, digits, underscores or hyphens"
        )
    return project


def _validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    return kind


def _validate_record_id(record_id: str) -> str:
    if not isinstance(record_id, str) or not _RECORD_ID.fullmatch(record_id):
        raise ValueError("record_id must contain 1-128 letters, digits, dots, underscores or hyphens")
    if record_id in {".", ".."}:
        raise ValueError("record_id cannot be a path segment")
    return record_id


def _normalise_root(home: Path | None) -> Path:
    if home is None:
        configured = os.environ.get("DURESS_HOME")
        requested = Path(configured).expanduser() if configured else Path.home() / ".local/share/duress"
    else:
        requested = Path(home).expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    requested = Path(os.path.abspath(requested))
    if requested.is_symlink():
        raise UnsafeStoragePath(f"storage root is a symlink: {requested}")
    if requested.exists():
        if not requested.is_dir():
            raise UnsafeStoragePath(f"storage root is not a directory: {requested}")
        return requested.resolve(strict=True)
    # Resolve existing ancestors once. The managed root and its descendants are
    # then checked with lstat before every read/write and are never traversed
    # through a symlink created inside the storage tree.
    return requested.parent.resolve(strict=False) / requested.name


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _ensure_directory(path: Path) -> None:
    info = _lstat(path)
    if info is not None:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeStoragePath(f"expected a real directory: {path}")
        return
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = _lstat(path)
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UnsafeStoragePath(f"expected a real directory: {path}")
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _ensure_regular_or_missing(path: Path) -> None:
    info = _lstat(path)
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)):
        raise UnsafeStoragePath(f"expected a regular file or no file: {path}")


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError(f"record must contain finite JSON values: {exc}") from exc


def _digest(value: dict[str, Any], *, field: str = "sha256") -> str:
    unsigned = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _seal_record(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result.pop("sha256", None)
    result["sha256"] = _digest(result)
    return result


def _verify_record(record: Any, *, project: str, kind: str, record_id: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise CorruptRecordError("record must be a JSON object")
    if type(record.get("schema_version")) is not int or record["schema_version"] != SCHEMA_VERSION:
        raise CorruptRecordError("unsupported record schema_version")
    if record.get("project") != project or record.get("kind") != kind:
        raise CorruptRecordError("record project or kind does not match its location")
    if record.get("record_id") != record_id:
        raise CorruptRecordError("record id does not match its filename")
    if not isinstance(record.get("created_at"), str) or not isinstance(record.get("updated_at"), str):
        raise CorruptRecordError("record timestamps are missing")
    if not isinstance(record.get("payload"), dict):
        raise CorruptRecordError("record payload must be an object")
    if not isinstance(record.get("owner"), dict):
        raise CorruptRecordError("record owner is missing")
    try:
        expected = _digest(record)
        _canonical_json(record)
    except (TypeError, ValueError) as exc:
        raise CorruptRecordError(f"record contains invalid JSON values: {exc}") from exc
    if not isinstance(record.get("sha256"), str) or not hmac.compare_digest(expected, record["sha256"]):
        raise CorruptRecordError("record integrity check failed")
    return record


def _key_is_sensitive(key: str) -> bool:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    words = [word for word in re.split(r"[^A-Za-z0-9]+", expanded.lower()) if word]
    compact = "".join(words)
    return bool(set(words) & _SECRET_WORDS) or any(word in compact for word in _SECRET_WORDS)


def _redact_text(value: str) -> str:
    value = _PRIVATE_KEY.sub("[REDACTED]", value)
    value = _BEARER_VALUE.sub(r"\1[REDACTED]", value)
    value = _URL_PASSWORD.sub(r"\1[REDACTED]\2", value)
    return _QUERY_CREDENTIAL.sub(r"\1[REDACTED]", value)


def _redact(value: Any) -> Any:
    """Redact credential-bearing fields before local persistence/export.

    Configuration references are redacted too: they can reveal environment
    names or secret-manager paths even though they are not the secret value.
    """
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _key_is_sensitive(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _read_regular(path: Path) -> bytes:
    _ensure_regular_or_missing(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise UnsafeStoragePath(f"refusing symlink: {path}") from exc
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeStoragePath(f"expected a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: bytes) -> None:
    _ensure_directory(path.parent)
    _ensure_regular_or_missing(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _format_json(value: Any) -> bytes:
    _canonical_json(value)
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def _process_is_same_owner(pid: Any, start_time: Any) -> bool | None:
    """Return True for the recorded process, False for an orphan, else unknown."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if not isinstance(start_time, str) or not start_time:
        return None
    current = process_start_time(pid)
    if current is not None:
        if current != start_time:
            return False
        return _pid_is_runnable(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return None
        return None
    # The pid exists but the platform could not provide a start-time identity.
    return None


def _pid_is_runnable(pid: int) -> bool | None:
    """Distinguish an executing owner from a zombie process entry."""
    proc_stat = Path("/proc") / str(pid) / "stat"
    try:
        raw = proc_stat.read_text()
    except OSError:
        pass
    else:
        _, _, remainder = raw.rpartition(")")
        fields = remainder.split()
        if fields:
            return not fields[0].startswith("Z")
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return None
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return False
            return None
        return None
    return not result.stdout.strip().startswith("Z")


class ProjectStore:
    """Filesystem store isolated to one validated local project id.

    ``home`` is the store root (the same role as ``DURESS_HOME``), not the
    per-project directory. The absolute project directory is available as
    :attr:`home`.
    """

    def __init__(self, project: str, home: Path | None = None) -> None:
        self.project = _validate_project(project)
        self.root = _normalise_root(home)
        self.home = (self.root / "projects" / self.project).absolute()

    def _ensure_home(self) -> None:
        _ensure_directory(self.root)
        _ensure_directory(self.root / "projects")
        _ensure_directory(self.home)

    def _kind_dir(self, kind: str, *, create: bool = False) -> Path:
        kind = _validate_kind(kind)
        if create:
            self._ensure_home()
            _ensure_directory(self.home / kind)
        else:
            self._ensure_home()
            path = self.home / kind
            info = _lstat(path)
            if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)):
                raise UnsafeStoragePath(f"expected a real directory: {path}")
        return self.home / kind

    def _path(self, kind: str, record_id: str, *, create: bool = False) -> Path:
        kind = _validate_kind(kind)
        record_id = _validate_record_id(record_id)
        return self._kind_dir(kind, create=create) / f"{record_id}.json"

    def save(self, kind: str, payload: dict[str, Any], record_id: str | None = None) -> Path:
        """Atomically save a versioned JSON record and return its absolute path."""
        kind = _validate_kind(kind)
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        record_id = _validate_record_id(record_id or f"{int(time.time() * 1000)}-{uuid.uuid4().hex}")
        path = self._path(kind, record_id, create=True)
        created_at = _utc_now()
        existing = _lstat(path)
        if existing is not None:
            old = self._read(kind, record_id, missing_ok=True)
            if old is not None:
                created_at = old["created_at"]
        now = _utc_now()
        safe_payload = _redact(payload)
        _canonical_json(safe_payload)
        status = safe_payload.get("status")
        if not isinstance(status, str):
            status = safe_payload.get("state") if isinstance(safe_payload.get("state"), str) else None
        record = {
            "schema_version": SCHEMA_VERSION,
            "project": self.project,
            "kind": kind,
            "record_id": record_id,
            "created_at": created_at,
            "updated_at": now,
            "status": status,
            "owner": {"pid": os.getpid(), "start_time": process_start_time(os.getpid())},
            "payload": safe_payload,
        }
        _atomic_write(path, _format_json(_seal_record(record)))
        return path

    def _read(self, kind: str, record_id: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        path = self._path(kind, record_id)
        try:
            raw = _read_regular(path)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        except UnsafeStoragePath:
            raise
        except OSError as exc:
            raise CorruptRecordError(f"cannot read record {record_id}: {exc}") from exc
        try:
            record = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptRecordError(f"record {record_id} is not complete JSON") from exc
        return _verify_record(record, project=self.project, kind=kind, record_id=record_id)

    def load(self, kind: str, record_id: str) -> dict[str, Any]:
        """Load one validated record envelope."""
        return self._read(kind, record_id)  # type: ignore[return-value]

    def list(self, kind: str) -> list[dict[str, Any]]:
        """Return valid records in newest-created-first order.

        Corrupt and partial JSON is skipped so one damaged file does not hide
        the rest of a project's history. :meth:`diagnostics` reports skips.
        """
        directory = self._kind_dir(kind)
        if not directory.exists():
            return []
        records: list[dict[str, Any]] = []
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries if entry.name.endswith(".json"))
        for name in names:
            record_id = name[:-5]
            if not _RECORD_ID.fullmatch(record_id) or record_id in {".", ".."}:
                continue
            try:
                record = self._read(kind, record_id)
            except (CorruptRecordError, FileNotFoundError, UnsafeStoragePath, OSError):
                continue
            if record is not None:
                records.append(record)
        return sorted(records, key=lambda row: (row["created_at"], row["record_id"]), reverse=True)

    def diagnostics(self) -> dict[str, Any]:
        """Summarize record counts and damaged paths without returning payloads."""
        self._ensure_home()
        counts: dict[str, int] = {}
        invalid: list[str] = []
        symlinks: list[str] = []
        for kind in KINDS:
            directory = self.home / kind
            info = _lstat(directory)
            if info is None:
                counts[kind] = 0
                continue
            if stat.S_ISLNK(info.st_mode):
                symlinks.append(kind)
                counts[kind] = 0
                continue
            if not stat.S_ISDIR(info.st_mode):
                invalid.append(kind)
                counts[kind] = 0
                continue
            valid_count = 0
            with os.scandir(directory) as entries:
                paths = sorted((Path(entry.path) for entry in entries if entry.name.endswith(".json")), key=str)
            for path in paths:
                if path.is_symlink():
                    symlinks.append(path.relative_to(self.home).as_posix())
                    continue
                record_id = path.name[:-5]
                if not _RECORD_ID.fullmatch(record_id) or record_id in {".", ".."}:
                    invalid.append(path.relative_to(self.home).as_posix())
                    continue
                try:
                    self._read(kind, record_id)
                    valid_count += 1
                except (CorruptRecordError, FileNotFoundError, UnsafeStoragePath, OSError):
                    invalid.append(path.relative_to(self.home).as_posix())
            counts[kind] = valid_count
        return {
            "schema_version": SCHEMA_VERSION,
            "project": self.project,
            "root": str(self.root),
            "home": str(self.home),
            "counts": counts,
            "total_records": sum(counts.values()),
            "invalid_records": invalid,
            "symlinks": symlinks,
        }

    def reconcile(self) -> dict[str, Any]:
        """Mark only provably orphaned ``running`` records as interrupted.

        A matching PID and process start-time is left untouched. If the process
        table cannot establish identity, the record is left untouched as well.
        """
        changed: list[str] = []
        uncertain: list[str] = []
        for kind in KINDS:
            directory = self._kind_dir(kind)
            if not directory.exists():
                continue
            with os.scandir(directory) as entries:
                names = sorted(entry.name for entry in entries if entry.name.endswith(".json"))
            for name in names:
                record_id = name[:-5]
                if not _RECORD_ID.fullmatch(record_id):
                    continue
                try:
                    record = self._read(kind, record_id)
                except (CorruptRecordError, FileNotFoundError, UnsafeStoragePath, OSError):
                    continue
                if record is None:
                    continue
                payload = record["payload"]
                running = (record.get("status") in {"queued", "running"}
                           or payload.get("status") in {"queued", "running"}
                           or payload.get("state") in {"queued", "running"})
                if not running:
                    continue
                owner = record["owner"]
                same = _process_is_same_owner(owner.get("pid"), owner.get("start_time"))
                label = f"{kind}/{record_id}"
                if same is True:
                    continue
                if same is None:
                    uncertain.append(label)
                    continue
                record["status"] = "interrupted"
                if payload.get("status") in {"queued", "running"}:
                    payload["status"] = "interrupted"
                if payload.get("state") in {"queued", "running"}:
                    payload["state"] = "interrupted"
                record["updated_at"] = _utc_now()
                _atomic_write(self._path(kind, record_id), _format_json(_seal_record(record)))
                changed.append(label)
        return {"interrupted": changed, "uncertain": uncertain, "count": len(changed)}

    def cleanup(self, retain_days: int, dry_run: bool = True) -> dict[str, Any]:
        """Remove valid records older than ``retain_days``; preview by default."""
        if isinstance(retain_days, bool) or not isinstance(retain_days, int) or retain_days < 0:
            raise ValueError("retain_days must be a non-negative integer")
        cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
        removed: list[str] = []
        skipped: list[str] = []
        for kind in KINDS:
            directory = self._kind_dir(kind)
            if not directory.exists():
                continue
            with os.scandir(directory) as entries:
                names = sorted(entry.name for entry in entries if entry.name.endswith(".json"))
            for name in names:
                record_id = name[:-5]
                if not _RECORD_ID.fullmatch(record_id):
                    continue
                path = self._path(kind, record_id)
                if path.is_symlink():
                    skipped.append(f"{kind}/{record_id}:symlink")
                    continue
                try:
                    record = self._read(kind, record_id)
                    updated = datetime.fromisoformat(record["updated_at"])
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                except (CorruptRecordError, FileNotFoundError, UnsafeStoragePath, OSError, ValueError):
                    skipped.append(f"{kind}/{record_id}:invalid")
                    continue
                if updated >= cutoff:
                    continue
                label = f"{kind}/{record_id}"
                if not dry_run:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        continue
                removed.append(label)
        return {
            "dry_run": dry_run,
            "retain_days": retain_days,
            "cutoff": cutoff.isoformat(timespec="seconds"),
            "removed": removed,
            "count": len(removed),
            "skipped": skipped,
        }

    def export_to(self, destination: Path) -> Path:
        """Export valid local records to an explicitly named JSON bundle file."""
        return export_records(self, destination)

    def import_from(self, source: Path, destination: Path) -> dict[str, Any]:
        """Import a bundle into an explicitly selected destination store root."""
        try:
            bundle = json.loads(_read_regular(Path(source).expanduser()))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptRecordError("export bundle is not complete JSON") from exc
        if not isinstance(bundle, dict) or bundle.get("project") != self.project:
            raise ValueError("import bundle project does not match this ProjectStore")
        result = import_records(source, destination)
        return result


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    _ensure_directory(path.parent)
    _ensure_regular_or_missing(path)
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafeStoragePath(f"expected a regular lock file: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _registry_path(root: Path) -> Path:
    return root / "registry.json"


def _load_registry(root: Path, *, missing_ok: bool = True) -> dict[str, Any]:
    path = _registry_path(root)
    try:
        raw = _read_regular(path)
    except FileNotFoundError:
        if missing_ok:
            return {"schema_version": SCHEMA_VERSION, "projects": {}}
        raise
    try:
        registry = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptRecordError("project registry is not complete JSON") from exc
    if (
        not isinstance(registry, dict)
        or registry.get("schema_version") != SCHEMA_VERSION
        or not isinstance(registry.get("projects"), dict)
    ):
        raise CorruptRecordError("project registry has an unsupported shape")
    return registry


def register_project(config: Path, home: Path | None = None) -> dict[str, Any]:
    """Validate and register a local manifest by project id and absolute path."""
    config_path = Path(config).expanduser().resolve(strict=True)
    if not config_path.is_file():
        raise ValueError(f"manifest is not a regular file: {config_path}")
    try:
        manifest = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read manifest {config_path}: {exc}") from exc
    duress = manifest.get("duress") if isinstance(manifest, dict) else None
    if not isinstance(duress, dict):
        raise ValueError("manifest must declare a [duress] table")
    version = duress.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError(f"unsupported duress.schema_version: {version!r}")
    project = _validate_project(duress.get("project"))
    root = _normalise_root(home)
    _ensure_directory(root)
    store = ProjectStore(project, root)
    store._ensure_home()
    now = _utc_now()
    with _exclusive_lock(root / ".registry.lock"):
        registry = _load_registry(root)
        projects = registry["projects"]
        prior = projects.get(project)
        registered_at = prior.get("registered_at", now) if isinstance(prior, dict) else now
        projects[project] = {
            "project": project,
            "config_path": str(config_path),
            "registered_at": registered_at,
            "updated_at": now,
        }
        _atomic_write(_registry_path(root), _format_json(registry))
    return dict(projects[project])


def registered_projects(home: Path | None = None) -> list[dict[str, Any]]:
    """Return the safe registry projection; manifests and credentials are not loaded."""
    root = _normalise_root(home)
    if not root.exists():
        return []
    info = _lstat(root)
    if info is None:
        return []
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UnsafeStoragePath(f"expected a real storage root: {root}")
    with _exclusive_lock(root / ".registry.lock"):
        projects = _load_registry(root)["projects"]
    safe: list[dict[str, Any]] = []
    for key, item in projects.items():
        try:
            project = _validate_project(key)
        except ValueError:
            continue
        if not isinstance(item, dict) or item.get("project") != project:
            continue
        config_path = item.get("config_path")
        registered_at = item.get("registered_at")
        updated_at = item.get("updated_at")
        if not isinstance(config_path, str) or not Path(config_path).is_absolute():
            continue
        safe.append(
            {
                "project": project,
                "config_path": config_path,
                "registered_at": registered_at if isinstance(registered_at, str) else None,
                "updated_at": updated_at if isinstance(updated_at, str) else None,
            }
        )
    return sorted(safe, key=lambda item: item["project"])


def export_records(store: ProjectStore, destination: Path) -> Path:
    """Write a digest-checked export bundle to the explicit destination file."""
    destination = Path(destination).expanduser()
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination = Path(os.path.abspath(destination))
    if destination.is_symlink():
        raise UnsafeStoragePath(f"export destination is a symlink: {destination}")
    _ensure_directory(destination.parent)
    records: list[dict[str, Any]] = []
    for kind in KINDS:
        for record in store.list(kind):
            record = dict(record)
            record["payload"] = _redact(record["payload"])
            record = _seal_record(record)
            records.append(
                {
                    "kind": kind,
                    "record_id": record["record_id"],
                    "sha256": record["sha256"],
                    "record": record,
                }
            )
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "project": store.project,
        "exported_at": _utc_now(),
        "records": records,
    }
    bundle["sha256"] = _digest(bundle)
    _atomic_write(destination, _format_json(bundle))
    return destination


def import_records(source: Path, destination: Path) -> dict[str, Any]:
    """Verify a bundle and import it under ``destination/projects/{project}``.

    ``destination`` is an explicit store root, not a per-project directory.
    Existing identical ids are kept; conflicting ids fail unless callers first
    choose a fresh destination root.
    """
    source = Path(source).expanduser()
    raw = _read_regular(source)
    try:
        bundle = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptRecordError("export bundle is not complete JSON") from exc
    if not isinstance(bundle, dict) or type(bundle.get("schema_version")) is not int or bundle["schema_version"] != SCHEMA_VERSION:
        raise CorruptRecordError("unsupported export bundle schema_version")
    project = _validate_project(bundle.get("project"))
    if not isinstance(bundle.get("records"), list):
        raise CorruptRecordError("export bundle records must be a list")
    expected_bundle_digest = _digest(bundle)
    if not isinstance(bundle.get("sha256"), str) or not hmac.compare_digest(
        expected_bundle_digest, bundle["sha256"]
    ):
        raise CorruptRecordError("export bundle integrity check failed")
    store = ProjectStore(project, Path(destination))
    verified: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for entry in bundle["records"]:
        if not isinstance(entry, dict):
            raise CorruptRecordError("export bundle contains an invalid record entry")
        kind = _validate_kind(entry.get("kind"))
        record_id = _validate_record_id(entry.get("record_id"))
        key = (kind, record_id)
        if key in seen:
            raise CorruptRecordError(f"export bundle repeats record: {kind}/{record_id}")
        seen.add(key)
        record = _verify_record(entry.get("record"), project=project, kind=kind, record_id=record_id)
        if entry.get("sha256") != record["sha256"]:
            raise CorruptRecordError(f"record entry digest mismatch: {kind}/{record_id}")
        verified.append((kind, record_id, record))
    store._ensure_home()
    prepared: list[tuple[str, str, Path, dict[str, Any]]] = []
    imported: list[str] = []
    existing: list[str] = []
    for kind, record_id, record in verified:
        path = store._path(kind, record_id, create=True)
        current = store._read(kind, record_id, missing_ok=True)
        safe_record = dict(record)
        safe_record["payload"] = _redact(safe_record["payload"])
        safe_record = _seal_record(safe_record)
        if current is not None:
            if current == safe_record:
                existing.append(f"{kind}/{record_id}")
                continue
            raise CorruptRecordError(f"destination already has a different record: {kind}/{record_id}")
        prepared.append((kind, record_id, path, safe_record))
    for kind, record_id, path, safe_record in prepared:
        _atomic_write(path, _format_json(safe_record))
        imported.append(f"{kind}/{record_id}")
    return {
        "project": project,
        "destination": str(store.home),
        "imported": imported,
        "existing": existing,
        "count": len(imported),
    }


__all__ = [
    "CorruptRecordError",
    "ProjectStore",
    "StorageError",
    "UnsafeStoragePath",
    "export_records",
    "import_records",
    "register_project",
    "registered_projects",
]
