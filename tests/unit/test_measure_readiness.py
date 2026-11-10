"""scripts/measure_readiness.py summarises recorded phase timings without executing anything."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import measure_readiness  # noqa: E402


def row(readiness, status="PASS", reasons=()):
    return {"trial_id": "t", "status": status, "reasons": list(reasons),
            "phase_timings": {"prepare": 0.2, "readiness": readiness, "execute": 0.3}}


def record(*rows):
    return {"scenarios": [{"scenario_id": "S1", "trials": list(rows)}]}


def test_report_flags_a_readiness_timeout_and_the_headroom_of_ordinary_starts():
    rows = measure_readiness.trial_rows(record(
        row(0.5), row(0.6), row(4.0), row(9.0),
        row(10.0, "INCONCLUSIVE", ["harness:target_unavailable"])))
    report = measure_readiness.build_report(rows, budget_s=10.0, source={"kind": "record"})
    readiness = report["readiness"]
    assert (readiness["n"], readiness["min"], readiness["max"]) == (5, 0.5, 10.0)
    assert readiness["median"] == 4.0
    assert readiness["timed_out"] == 1
    assert readiness["at_or_over_80_percent_of_budget"] == 2
    assert readiness["max_over_budget"] == 1.0
    assert report["target_unavailable_trials"] == 1
    assert report["statuses"] == {"PASS": 4, "INCONCLUSIVE": 1}
    assert report["phases"]["execute"]["n"] == 5


def test_records_without_timings_are_not_measurable(tmp_path, capsys):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"scenarios": [{"trials": [{"trial_id": "t", "status": "PASS"}]}]}))
    code = measure_readiness.main(["--from-record", str(legacy)])
    assert code == 2
    assert "no trial rows carry phase_timings" in capsys.readouterr().err


def test_from_record_writes_a_report_and_never_runs_a_trial(tmp_path):
    source = tmp_path / "inv.json"
    source.write_text(json.dumps(record(row(0.4), row(0.5))))
    out = tmp_path / "report.json"
    assert measure_readiness.main(["--from-record", str(source), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["schema"] == measure_readiness.SCHEMA
    assert report["source"] == {"kind": "record", "file": "inv.json"}
    assert report["readiness_budget_s"] == 10.0


def test_unreadable_record_is_reported_not_raised(tmp_path, capsys):
    assert measure_readiness.main(["--from-record", str(tmp_path / "absent.json")]) == 2
    assert "cannot read" in capsys.readouterr().err
