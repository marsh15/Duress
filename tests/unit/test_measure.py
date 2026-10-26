from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("duress_measure", ROOT / "scripts" / "measure.py")
assert SPEC is not None and SPEC.loader is not None
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


def test_parse_junit_counts_passed_failed_errors_and_skipped(tmp_path: Path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<testsuites>
          <testsuite tests="5" failures="1" errors="1" skipped="1">
            <testcase/><testcase/><testcase/><testcase/><testcase/>
          </testsuite>
        </testsuites>"""
    )

    assert measure.parse_junit(junit) == {
        "collected": 5,
        "passed": 2,
        "failed": 1,
        "errors": 1,
        "skipped": 1,
    }


def test_parse_junit_missing_file_keeps_counts_unknown(tmp_path: Path):
    assert measure.parse_junit(tmp_path / "missing.xml") == {
        "collected": None,
        "passed": None,
        "failed": None,
        "errors": None,
        "skipped": None,
    }


def test_render_preserves_historical_content_and_reports_only_recorded_runs():
    historical = "# Existing evidence\n\nM0 and M2 observed data.\n"
    rendered = measure.render(
        historical=historical,
        records=[
            {
                "run_id": "release-test",
                "generated_at": "2026-10-26T12:00:00+00:00",
                "environment": {"python": "3.12.14", "sqlite": "3.53.1", "platform": "test-os"},
                "suites": [
                    {
                        "name": "fast",
                        "command": "uv run pytest -q -m 'not slow'",
                        "exit_code": 1,
                        "seconds": 12.5,
                        "counts": {"passed": 10, "failed": 1, "errors": 0, "skipped": 2},
                        "junit_xml": ".duress/measurements/test/junit.xml",
                    }
                ],
            }
        ],
    )

    assert rendered.startswith(historical.rstrip() + "\n\n")
    assert "release-test" in rendered
    assert "| fast |" in rendered
    assert "| 10 | 1 | 0 | 2 |" in rendered
    assert "No M6 release runs have been recorded" not in rendered


def test_render_says_not_run_when_no_release_record_exists():
    rendered = measure.render(historical="# History\n", records=[])

    assert "# History" in rendered
    assert "No M6 release runs have been recorded" in rendered
    assert "have not run" in rendered


def test_empty_redirected_history_is_rebuilt_without_workspace_writes(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "m0_evidence.py").write_text(
        "from pathlib import Path\n"
        "def render():\n"
        "    target = ROOT / 'docs' / 'EVIDENCE.md'\n"
        "    target.parent.mkdir(parents=True, exist_ok=True)\n"
        "    target.write_text('# Rebuilt historical evidence\\n')\n"
    )
    historical = tmp_path / "EVIDENCE.md"
    historical.write_text("")
    measurements = tmp_path / "measurements"
    monkeypatch.setattr(measure, "ROOT", root)
    monkeypatch.setattr(measure, "MEASUREMENTS", measurements)
    monkeypatch.setattr(measure, "HISTORICAL_EVIDENCE", historical)

    assert measure.historical_document() == "# Rebuilt historical evidence\n"
    assert not (root / "docs" / "EVIDENCE.md").exists()


def test_custom_pytest_suite_requires_a_path_and_owns_junit_argument():
    with pytest.raises(ValueError, match="needs at least one"):
        measure.suite_args("pytest", [])
    with pytest.raises(ValueError, match="managed by measure.py"):
        measure.suite_args("fast", ["--junitxml=custom.xml"])
    assert measure.suite_args("pytest", ["tests/unit/test_limits.py"]) == [
        "tests/unit/test_limits.py"
    ]
