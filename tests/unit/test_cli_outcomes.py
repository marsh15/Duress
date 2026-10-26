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
