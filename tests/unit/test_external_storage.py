from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from duress.external import storage
from duress.external.storage import (
    CorruptRecordError,
    ProjectStore,
    UnsafeStoragePath,
    export_records,
    import_records,
    register_project,
    registered_projects,
)


def test_store_root_defaults_to_duress_home_then_local_share(tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path / "env-home"))
    configured = ProjectStore("tickets")
    assert configured.home == (tmp_path / "env-home" / "projects" / "tickets").absolute()

    monkeypatch.delenv("DURESS_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    defaulted = ProjectStore("tickets")
    assert defaulted.home == (tmp_path / ".local/share/duress/projects/tickets").absolute()


@pytest.mark.parametrize("project", ["", "../tickets", "Ticketing", "two.projects", ".", "a" * 64])
def test_project_id_rejects_paths_and_non_simple_ids(project):
    with pytest.raises(ValueError, match="project"):
        ProjectStore(project, Path("/tmp/duress-test-home"))


def test_projects_are_isolated_and_records_are_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: "pid-start")
    first = ProjectStore("ticket-service", tmp_path / "state")
    second = ProjectStore("billing-service", tmp_path / "state")
    old_path = first.save("investigations", {"status": "complete", "n": 1}, "run-old")
    new_path = first.save("investigations", {"status": "complete", "n": 2}, "run-new")
    second_path = second.save("investigations", {"status": "complete", "n": 3}, "run-new")

    assert old_path != second_path
    assert new_path.parent == old_path.parent
    assert [row["record_id"] for row in first.list("investigations")] == ["run-new", "run-old"]
    assert [row["record_id"] for row in second.list("investigations")] == ["run-new"]
    assert first.load("investigations", "run-old")["payload"]["n"] == 1


def test_concurrent_record_writes_are_complete_and_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: "worker-start")
    store = ProjectStore("concurrent", tmp_path / "state")

    def write(index: int) -> str:
        store.save("operations", {"status": "complete", "index": index}, f"op-{index}")
        return f"op-{index}"

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(write, range(60)))

    assert len(set(ids)) == 60
    records = store.list("operations")
    assert len(records) == 60
    assert {record["payload"]["index"] for record in records} == set(range(60))
    assert store.diagnostics()["total_records"] == 60


def test_same_record_updates_replace_atomically_and_keep_creation_time(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: "start")
    store = ProjectStore("updates", tmp_path / "state")
    path = store.save("dna", {"status": "running", "value": 1}, "dna-1")
    original = store.load("dna", "dna-1")
    store.save("dna", {"status": "complete", "value": 2}, "dna-1")
    current = store.load("dna", "dna-1")

    assert current["created_at"] == original["created_at"]
    assert current["payload"]["value"] == 2
    assert current["status"] == "complete"
    assert not list(path.parent.glob("*.tmp"))


def test_partial_and_corrupt_records_are_skipped_and_reported(tmp_path):
    store = ProjectStore("corrupt", tmp_path / "state")
    directory = store.home / "dna"
    directory.mkdir(parents=True)
    (directory / "partial.json").write_text('{"schema_version":')
    (directory / "bad-digest.json").write_text(
        json.dumps({"schema_version": 1, "project": "corrupt", "record_id": "bad-digest"})
    )

    assert store.list("dna") == []
    diagnostics = store.diagnostics()
    assert diagnostics["counts"]["dna"] == 0
    assert set(diagnostics["invalid_records"]) == {
        "dna/partial.json",
        "dna/bad-digest.json",
    }
    with pytest.raises(CorruptRecordError):
        store.load("dna", "partial")


def test_symlinked_record_and_kind_directory_are_never_followed(tmp_path):
    store = ProjectStore("links", tmp_path / "state")
    target = ProjectStore("target", tmp_path / "state")
    target_path = target.save("dna", {"status": "complete"}, "dna-1")
    links_dir = store.home / "dna"
    links_dir.mkdir(parents=True)
    (links_dir / "linked.json").symlink_to(target_path)

    assert store.list("dna") == []
    with pytest.raises(UnsafeStoragePath):
        store.load("dna", "linked")
    assert store.diagnostics()["symlinks"] == ["dna/linked.json"]

    (store.home / "operations").symlink_to(target.home / "dna", target_is_directory=True)
    with pytest.raises(UnsafeStoragePath):
        store.list("operations")


def test_reconcile_interrupts_only_proven_orphans_and_preserves_live_work(tmp_path, monkeypatch):
    owner_times = {os.getpid(): "start-one"}
    monkeypatch.setattr(storage, "process_start_time", lambda pid: owner_times.get(pid))
    store = ProjectStore("reconcile", tmp_path / "state")
    store.save("investigations", {"status": "running", "step": 1}, "live")
    store.save("investigations", {"status": "running", "step": 2}, "orphan")

    orphan_pid = 2_000_000_000
    orphan_path = store.home / "investigations" / "orphan.json"
    orphan = json.loads(orphan_path.read_text())
    orphan["owner"] = {"pid": orphan_pid, "start_time": "old-process"}
    orphan = storage._seal_record(orphan)
    orphan_path.write_text(json.dumps(orphan))

    result = store.reconcile()
    assert result["interrupted"] == ["investigations/orphan"]
    assert store.load("investigations", "live")["status"] == "running"
    interrupted = store.load("investigations", "orphan")
    assert interrupted["status"] == "interrupted"
    assert interrupted["payload"]["status"] == "interrupted"


def test_reconcile_leaves_running_records_when_process_identity_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: None)
    store = ProjectStore("unknown-owner", tmp_path / "state")
    store.save("operations", {"status": "running"}, "unknown")

    result = store.reconcile()

    assert result["interrupted"] == []
    assert result["uncertain"] == ["operations/unknown"]
    assert store.load("operations", "unknown")["status"] == "running"


def test_cleanup_defaults_to_dry_run_and_removes_old_valid_records(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: "start")
    store = ProjectStore("retention", tmp_path / "state")
    store.save("operations", {"status": "complete"}, "old")

    preview = store.cleanup(retain_days=0)
    assert preview["dry_run"] is True
    assert preview["removed"] == ["operations/old"]
    assert store.load("operations", "old")["record_id"] == "old"

    result = store.cleanup(retain_days=0, dry_run=False)
    assert result["count"] == 1
    assert store.list("operations") == []


def test_save_redacts_secret_values_and_configuration_references(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: "start")
    store = ProjectStore("privacy", tmp_path / "state")
    path = store.save(
        "investigations",
        {
            "status": "complete",
            "api_key": "raw-secret-value",
            "secret_ref": "env:DURESS_SECRET",
            "headers": {"Authorization": "Bearer raw-access-token"},
            "message": "postgresql://user:db-password@localhost/test",
        },
        "private-run",
    )

    content = path.read_text()
    record = store.load("investigations", "private-run")
    assert "raw-secret-value" not in content
    assert "DURESS_SECRET" not in content
    assert "raw-access-token" not in content
    assert "db-password" not in content
    assert record["payload"]["api_key"] == "[REDACTED]"
    assert record["payload"]["secret_ref"] == "[REDACTED]"


def test_export_import_verifies_integrity_and_uses_explicit_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: "start")
    source_store = ProjectStore("portable", tmp_path / "source")
    source_store.save("dna", {"status": "complete", "signature": {"kind": "race"}}, "dna-a")
    export_path = export_records(source_store, tmp_path / "exports" / "portable.json")

    result = import_records(export_path, tmp_path / "destination")
    imported_store = ProjectStore("portable", tmp_path / "destination")
    assert result["imported"] == ["dna/dna-a"]
    assert imported_store.load("dna", "dna-a")["payload"]["signature"]["kind"] == "race"
    assert import_records(export_path, tmp_path / "destination")["existing"] == ["dna/dna-a"]

    tampered = tmp_path / "tampered.json"
    bundle = json.loads(export_path.read_text())
    bundle["records"][0]["record"]["payload"]["signature"]["kind"] = "changed"
    tampered.write_text(json.dumps(bundle))
    with pytest.raises(CorruptRecordError, match="integrity"):
        import_records(tampered, tmp_path / "tampered-destination")
    assert not (tmp_path / "tampered-destination").exists()


def test_registration_stores_only_project_and_local_config_path(tmp_path):
    config = tmp_path / "duress.toml"
    config.write_text(
        '[duress]\nschema_version = 1\nproject = "ticket-service"\n'
        '[secrets]\nsecret = "do-not-store-this"\n'
    )
    home = tmp_path / "state"

    registered = register_project(config, home)

    assert registered["project"] == "ticket-service"
    assert registered["config_path"] == str(config.resolve())
    assert registered_projects(home) == [registered]
    assert "do-not-store-this" not in (home / "registry.json").read_text()
    assert (home / "projects" / "ticket-service").is_dir()


def test_registration_rejects_invalid_manifest_project(tmp_path):
    config = tmp_path / "duress.toml"
    config.write_text('[duress]\nproject = "../outside"\n')
    with pytest.raises(ValueError, match="project"):
        register_project(config, tmp_path / "state")


def test_concurrent_registration_keeps_every_project(tmp_path):
    home = tmp_path / "state"
    configs = []
    for index in range(12):
        config = tmp_path / f"project-{index}.toml"
        config.write_text(f'[duress]\nproject = "project-{index}"\n')
        configs.append(config)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda path: register_project(path, home), configs))

    assert {item["project"] for item in registered_projects(home)} == {
        f"project-{index}" for index in range(12)
    }


def test_export_destination_symlink_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "process_start_time", lambda pid: "start")
    store = ProjectStore("export-links", tmp_path / "state")
    store.save("dna", {"status": "complete"}, "dna-1")
    target = tmp_path / "actual.json"
    target.write_text("leave me alone")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(UnsafeStoragePath):
        store.export_to(link)
    assert target.read_text() == "leave me alone"
