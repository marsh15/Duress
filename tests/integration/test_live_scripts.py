"""Behaviour of scripts/live_llm_check.py and scripts/compare_modes.py (MOCK PROVIDER ONLY).

Every provider call here goes to the local mock in scripts/mock_openai_server.py.
Results are labelled mock-provider; none is evidence about a hosted model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import compare_modes  # noqa: E402
import live_llm_check  # noqa: E402
from mock_openai_server import MockOpenAIServer, Reply, proposal_text, vulnerable_proposal  # noqa: E402

API_KEY = "mock-secret-live-script"


def parse(argv: list[str]):
    return live_llm_check.build_parser().parse_args(argv)


# --------------------------------------------------------------------------- #
# live_llm_check
# --------------------------------------------------------------------------- #
def test_missing_env_exits_2_and_names_the_variables(tmp_path, capsys):
    out = tmp_path / "result.json"
    code = live_llm_check.run(parse(["--out", str(out)]), env={})
    assert code == 2
    err = capsys.readouterr().err
    assert "DURESS_LLM_BASE_URL" in err and "DURESS_LLM_MODEL" in err
    assert "no request was sent" in err
    assert not out.exists()


def test_blank_model_counts_as_missing(tmp_path):
    env = {"DURESS_LLM_BASE_URL": "http://127.0.0.1:9/v1", "DURESS_LLM_MODEL": "   "}
    assert live_llm_check.missing_env_message(env) is not None


def test_evidence_class_labels_loopback_as_mock_and_others_external():
    assert live_llm_check.evidence_class("http://127.0.0.1:8000/v1") == "mock-provider"
    assert live_llm_check.evidence_class("http://localhost:1234/v1") == "mock-provider"
    assert live_llm_check.evidence_class("https://api.example.test/v1") == "external-provider"


def test_live_check_against_mock_accepts_and_redacts(tmp_path, capsys):
    out = tmp_path / "live.json"
    env = {
        "DURESS_LLM_API_KEY": API_KEY,
        "DURESS_LLM_MODEL": "mock-model",
    }
    with MockOpenAIServer([Reply.ok(proposal_text(vulnerable_proposal()))]) as server:
        env["DURESS_LLM_BASE_URL"] = server.base_url
        code = live_llm_check.run(parse(["--out", str(out), "--wall-cap", "0"]), env=env)

    assert code == 0, capsys.readouterr().err
    text = out.read_text()
    assert API_KEY not in text
    result = json.loads(text)
    assert result["schema"] == live_llm_check.SCHEMA
    assert result["evidence_class"] == "mock-provider"
    assert result["live_path_accepted"] is True
    assert result["summary"]["mode_effective"] == "live"
    assert result["provider"] == {"base_url_host": "127.0.0.1", "model": "mock-model", "api_key": "set"}


def test_live_check_exits_1_on_fallback_and_still_writes_the_result(tmp_path):
    out = tmp_path / "fallback.json"
    env = {"DURESS_LLM_MODEL": "mock-model"}
    with MockOpenAIServer([Reply.error(500, "down")]) as server:
        env["DURESS_LLM_BASE_URL"] = server.base_url
        code = live_llm_check.run(parse(["--out", str(out), "--wall-cap", "0"]), env=env)

    assert code == 1
    result = json.loads(out.read_text())
    assert result["live_path_accepted"] is False
    assert result["summary"]["fallback_reason"] == "transport_error"


# --------------------------------------------------------------------------- #
# compare_modes: pure helpers
# --------------------------------------------------------------------------- #
def test_template_of_strips_parameters():
    assert compare_modes.template_of("S3[copies=3,fault=rendezvous]") == "S3"
    assert compare_modes.template_of("S1") == "S1"


def test_scenario_coverage_counts_distinct_library_templates():
    record = {"selected_scenarios": ["S1", "S2", "S3[copies=3]", "S3[copies=2]", "S9"]}
    coverage = compare_modes.scenario_coverage(record, {"S1", "S2", "S3", "S5", "S6", "S8", "S9"})
    assert coverage["templates"] == ["S1", "S2", "S3", "S9"]
    assert coverage["scenario_count"] == 5
    assert coverage["library_size"] == 7
    assert coverage["fraction_of_library"] == round(4 / 7, 4)


def test_aggregate_summarises_completed_runs_and_ignores_errors():
    runs = [
        {"error": None, "elapsed_s": 2.0, "mode_effective": "live", "fallback_reason": None,
         "verdict": "FAIL", "model_calls": 1,
         "coverage": {"fraction_of_library": 0.5, "scenario_count": 4}},
        {"error": None, "elapsed_s": 4.0, "mode_effective": "exhaustive",
         "fallback_reason": "transport_error", "verdict": "INCONCLUSIVE", "model_calls": 1,
         "coverage": {"fraction_of_library": 0.25, "scenario_count": 2}},
        {"error": "RuntimeError: boom", "mode_requested": "live"},
    ]
    stats = compare_modes.aggregate(runs)
    assert stats["runs"] == 3
    assert stats["completed"] == 2
    assert stats["elapsed_s_mean"] == 3.0
    assert stats["fallback_reasons"] == {"transport_error": 1}
    assert stats["verdicts"] == {"FAIL": 1, "INCONCLUSIVE": 1}
    assert stats["coverage_fraction_mean"] == 0.375


def test_aggregate_with_no_completed_runs():
    assert compare_modes.aggregate([{"error": "x"}]) == {"runs": 1, "completed": 0}


# --------------------------------------------------------------------------- #
# compare_modes: CLI
# --------------------------------------------------------------------------- #
def test_compare_modes_rejects_unknown_mode(capsys):
    assert compare_modes.main(["--mock", "--modes", "live,fast", "--out", "/dev/null"]) == 2


def test_compare_modes_without_config_or_mock_exits_2(tmp_path, monkeypatch, capsys):
    for name in ("DURESS_LLM_BASE_URL", "DURESS_LLM_MODEL", "DURESS_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    out = tmp_path / "compare.json"
    assert compare_modes.main(["--modes", "live", "--out", str(out)]) == 2
    assert not out.exists()
    assert "missing environment variable" in capsys.readouterr().err


def test_compare_modes_mock_dry_run_writes_labelled_result(tmp_path):
    out = tmp_path / "compare.json"
    code = compare_modes.main(
        ["--mock", "--runs", "1", "--modes", "live", "--wall-cap", "0", "--out", str(out)]
    )
    assert code == 0
    payload = json.loads(out.read_text())
    assert payload["schema"] == compare_modes.SCHEMA
    assert payload["evidence_class"] == "mock-provider"
    [run] = payload["runs"]["live"]
    assert run["error"] is None
    assert run["mode_effective"] == "live"
    assert run["model_calls"] == 1
    assert payload["summary"]["live"]["completed"] == 1
