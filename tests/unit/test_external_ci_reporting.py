from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

from typer.testing import CliRunner

from duress.cli import app
from duress.external.reporting import html_report, junit


def _record(status: str, project: str = "owned-ticketing") -> dict:
    return {
        "schema_version": 1,
        "investigation_id": "i-ci-reporting",
        "project": project,
        "status": "completed",
        "verdict": status,
        "dna": [],
        "scenarios": [{
            "scenario_id": "concurrent-duplicate",
            "result": {"status": status, "valid": 3, "fail": int(status == "FAIL"),
                       "inconclusive": int(status == "INCONCLUSIVE"), "trials": 3},
            "trials": [],
        }],
    }


def test_junit_reports_fail_as_failure_and_inconclusive_as_skipped() -> None:
    failed = ElementTree.fromstring(junit(_record("FAIL")))
    inconclusive = ElementTree.fromstring(junit(_record("INCONCLUSIVE")))

    assert failed.attrib["tests"] == "1"
    assert failed.attrib["failures"] == "1"
    assert failed.attrib["skipped"] == "0"
    assert failed.find("testcase/failure") is not None

    assert inconclusive.attrib["tests"] == "1"
    assert inconclusive.attrib["failures"] == "0"
    assert inconclusive.attrib["skipped"] == "1"
    assert inconclusive.find("testcase/skipped") is not None


def test_check_exit_status_gates_fail_and_inconclusive_records(
    tmp_path: Path, monkeypatch,
) -> None:
    async def fake_investigate(config: Path, *, scenario_id: str | None = None) -> dict:
        assert scenario_id == "concurrent-duplicate"
        return _record("FAIL")

    monkeypatch.setattr("duress.external.services.investigate", fake_investigate)
    junit_path = tmp_path / "failed.xml"
    failed = CliRunner().invoke(app, [
        "check", "--config", str(tmp_path / "duress.toml"),
        "--scenario", "concurrent-duplicate", "--junit", str(junit_path), "--json",
    ])

    assert failed.exit_code == 1
    assert ElementTree.parse(junit_path).getroot().find("testcase/failure") is not None

    async def fake_inconclusive(config: Path, *, scenario_id: str | None = None) -> dict:
        return _record("INCONCLUSIVE")

    monkeypatch.setattr("duress.external.services.investigate", fake_inconclusive)
    junit_path = tmp_path / "inconclusive.xml"
    inconclusive = CliRunner().invoke(app, [
        "check", "--config", str(tmp_path / "duress.toml"),
        "--scenario", "concurrent-duplicate", "--junit", str(junit_path), "--json",
    ])

    assert inconclusive.exit_code == 2
    assert ElementTree.parse(junit_path).getroot().find("testcase/skipped") is not None


def test_check_configuration_error_uses_exit_three(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["check", "--config", str(tmp_path / "missing.toml"), "--json"])

    assert result.exit_code == 3
    assert json.loads(result.output)["outcome"] == "ERROR"


def test_rejected_external_replay_uses_exit_four(tmp_path: Path, monkeypatch) -> None:
    dna_path = tmp_path / "rejected.json"
    dna_path.write_text('{"schema":"external-dna@1"}', encoding="utf-8")

    async def rejected_replay(config: Path, payload: dict) -> dict:
        return {"outcome": "REJECTED", "reason": "incompatible external artifact", "executed": False}

    monkeypatch.setattr("duress.external.artifacts.replay", rejected_replay)
    result = CliRunner().invoke(app, [
        "replay", str(dna_path), "--config", str(tmp_path / "duress.toml"), "--json",
    ])

    assert result.exit_code == 4
    assert json.loads(result.output)["outcome"] == "REJECTED"


def test_html_report_escapes_project_and_record_content() -> None:
    report = html_report(_record("FAIL", project="<script>alert('&')</script>"))

    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert "&#x27;" in report
