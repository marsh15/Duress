"""A differential with incomplete evidence must not exit successfully."""
from __future__ import annotations

from typer.testing import CliRunner
from duress import cli, services


def test_inconclusive_differential_uses_the_inconclusive_exit_code(monkeypatch):
    async def differential(*args, **kwargs):
        return {
            "differential_id": "diff-inconclusive", "status": "completed", "cells": [],
            "summary": {"disagree": 0, "inconclusive": 1},
        }

    monkeypatch.setattr(cli, "_workspace", lambda: object())
    monkeypatch.setattr(services, "differential", differential)
    result = CliRunner().invoke(cli.app, ["differential", "--json"])
    assert result.exit_code == 2, result.output


def _home_with_showcase_dna(tmp_path, monkeypatch):
    """A runtime home holding the bundled artifact, so only the lookup under test can fail."""
    import shutil
    from pathlib import Path

    home = tmp_path / "home"
    (home / "dna").mkdir(parents=True)
    shutil.copy(Path(__file__).resolve().parents[2] / "showcase/dna/e8007dd56adae8ae.json",
                home / "dna" / "e8007dd56adae8ae.json")
    monkeypatch.setenv("DURESS_HOME", str(home))
    return "e8007dd56adae8ae"


def test_verify_fix_with_an_unknown_dna_is_an_operational_error_not_not_verified(tmp_path, monkeypatch):
    # Exit 1 means NOT_VERIFIED; a CI gate must be able to tell a typo from that verdict.
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))
    result = CliRunner().invoke(cli.app, ["verify-fix", "nosuchdna", "--json"])
    assert result.exit_code == 3, (result.exit_code, result.output)
    assert "nosuchdna" in result.output


def test_verify_fix_with_an_unknown_target_is_an_operational_error(tmp_path, monkeypatch):
    dna = _home_with_showcase_dna(tmp_path, monkeypatch)
    for flags in (["--candidate", "no-such-target"], ["--vulnerable", "no-such-target"]):
        result = CliRunner().invoke(cli.app, ["verify-fix", dna, *flags, "--json"])
        assert result.exit_code == 3, (flags, result.exit_code, result.output)
        assert "no-such-target" in result.output


def test_replay_with_an_unknown_target_is_an_operational_error(tmp_path, monkeypatch):
    dna = _home_with_showcase_dna(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli.app, ["replay", dna, "--target", "no-such-target", "--json"])
    assert result.exit_code == 3, (result.exit_code, result.output)
    assert "no-such-target" in result.output


def test_unreadable_project_manifest_is_an_operational_error(tmp_path):
    missing = str(tmp_path / "absent.toml")
    for argv in (["targets", "--config", missing], ["dna", "list", "--config", missing],
                 ["report", "inv-x", "--config", missing, "--output", str(tmp_path / "r.html")],
                 ["dna", "export", "x", "--config", missing, "--output", str(tmp_path / "x.json")],
                 ["project-store", "--config", missing]):
        result = CliRunner().invoke(cli.app, argv)
        assert result.exit_code == 3, (argv, result.exit_code, result.output)
