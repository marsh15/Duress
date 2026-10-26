"""Investigator: bundle, prompt, output validation and the three hypothesis checks (plan C.12)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from duress import limits
from duress.investigator import (
    KNOWN_KINDS,
    SCENARIO_LIBRARY,
    BundleTooLarge,
    Citation,
    HypothesisDraft,
    ModelOutput,
    OutputInvalid,
    PredictedFailure,
    ScenarioPick,
    SourceBundle,
    _EXAMPLE_OUTPUT,
    assess,
    build_messages,
    build_source_bundle,
    check_citations,
    check_linkage,
    check_structure,
    parse_model_output,
    repair_messages,
    resolve_outcome,
    scenario_roles,
    select_scenarios,
    template_explanation,
    validate_params,
)
from duress.models import (
    AggregateVerdict,
    FailureSignature,
    ScenarioResult,
    ScenarioSpec,
    TrialVerdict,
    Violation,
    canonical_sha256,
)
from duress.profiles import load_seed
from duress.scenarios import EXHAUSTIVE_SET, EXHAUSTIVE_SPECS, TEMPLATES, build_scenario

REPO = Path(__file__).resolve().parents[2]
VULN = "razorpay_ref/vulnerable/handler.py"
FIXED = "razorpay_ref/fixed/handler.py"
S3_ID = "S3[copies=3,fault=rendezvous,latency_ms=200]"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def profile(registry):
    return registry.get("razorpay-vulnerable")


@pytest.fixture(scope="module")
def bundle(profile) -> SourceBundle:
    return build_source_bundle(profile)


@pytest.fixture(scope="module")
def fixed_bundle(registry) -> SourceBundle:
    return build_source_bundle(registry.get("razorpay-fixed"))


@pytest.fixture(scope="module")
def seed(profile):
    return load_seed(profile)


@pytest.fixture(scope="module")
def roles(profile):
    return dict(profile.roles)


def _line_of(bundle: SourceBundle, path: str, needle: str) -> int:
    for number, line in enumerate(bundle.files[path], 1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not found in {path}")


def _draft(**overrides) -> HypothesisDraft:
    values = dict(
        id="H1",
        title="check-then-act race",
        mechanism="State is read, an await yields, then the write lands unguarded.",
        mechanism_kind="check_then_act_race",
        citations=[Citation(file=VULN, line_start=53, line_end=96)],
        predicted_failure=PredictedFailure(
            check="INV-1@1", kind="duplicate_fulfilment", role="order:A"
        ),
        scenarios=[ScenarioPick(template_id="S3", params={"copies": 3})],
    )
    values.update(overrides)
    return HypothesisDraft(**values)


def _vuln_citation(bundle, **overrides) -> Citation:
    start = _line_of(bundle, VULN, "T1: autocommit")
    end = _line_of(bundle, VULN, "end T2")
    values = dict(file=VULN, line_start=start, line_end=end)
    values.update(overrides)
    return Citation(**values)


def _violation(check="INV-1@1", kind="duplicate_fulfilment", roles=("order:A",)) -> Violation:
    return Violation(check=check, kind=kind, roles=list(roles))


def _verdict(status, violations=(), misses=()) -> TrialVerdict:
    return TrialVerdict(
        status=status, violations=list(violations), expected_effect_misses=list(misses)
    )


def _result(
    scenario_id,
    status,
    verdicts=(),
    *,
    valid=5,
    fail=0,
    signature=None,
) -> ScenarioResult:
    return ScenarioResult(
        template_id=scenario_id.split("[")[0],
        scenario_id=scenario_id,
        result=AggregateVerdict(
            status=status, valid=valid, fail=fail, trials=len(verdicts), signature=signature
        ),
        verdicts=list(verdicts),
    )


def _usable(bundle, roles, seed, **overrides):
    draft = _draft(citations=[_vuln_citation(bundle)], **overrides)
    hypothesis = assess(draft, bundle, roles, seed)
    assert hypothesis.usable, hypothesis.status
    return hypothesis


# --------------------------------------------------------------------------- #
# Source bundle
# --------------------------------------------------------------------------- #
def test_vulnerable_bundle_keeps_own_variant_and_common_only(bundle):
    names = set(bundle.files)
    assert VULN in names
    assert "razorpay_ref/common/db.py" in names
    assert "razorpay_ref/serve.py" in names
    assert not any(name.startswith("razorpay_ref/fixed/") for name in names)
    assert not any(name.startswith("razorpay_ref/naive_fix/") for name in names)
    assert not any("tests" in name.split("/") for name in names)
    assert not any("__pycache__" in name for name in names)
    assert not any(Path(name).name.startswith("test_") for name in names)


def test_fixed_bundle_drops_vulnerable_variant(fixed_bundle):
    names = set(fixed_bundle.files)
    assert FIXED in names
    assert not any(name.startswith("razorpay_ref/vulnerable/") for name in names)


def test_variant_named_differently_from_its_package_keeps_the_right_handler(registry):
    # razorpay-fixed-2w runs the fixed package under another variant name.
    names = set(build_source_bundle(registry.get("razorpay-fixed-2w")).files)
    assert FIXED in names
    assert VULN not in names


def test_fixture_modes_bundle_is_sensible(registry):
    bundle = build_source_bundle(registry.get("fixture-ack_without_effect"))
    assert "fixture_modes/app.py" in bundle.files
    assert all(name.startswith("fixture_modes/") for name in bundle.files)
    assert not any("__pycache__" in name for name in bundle.files)


def test_bundle_is_within_limit_and_deterministic(registry):
    for target in registry.all():
        first = build_source_bundle(target)
        second = build_source_bundle(target)
        assert first.sha256 == second.sha256 == canonical_sha256(first.files)
        assert 0 < first.total_bytes <= limits.MODEL_BUNDLE_BYTES
        assert list(first.files) == sorted(first.files)
        assert first.root == target.target_dir.parent


def test_bundle_line_model_matches_source(bundle):
    text = (REPO / "targets" / VULN).read_text()
    assert bundle.files[VULN] == text.splitlines()
    assert bundle.total_bytes == sum(
        len((bundle.root / name).read_bytes()) for name in bundle.files
    )


def test_render_is_line_numbered_and_sorted(bundle):
    rendered = bundle.render()
    first = bundle.files[VULN][0]
    assert f"=== {VULN} ===\n   1| {first}" in rendered
    line = _line_of(bundle, VULN, "T1: autocommit")
    assert f"{line:4d}| " + bundle.files[VULN][line - 1] in rendered
    headers = re.findall(r"^=== (.+) ===$", rendered, re.MULTILINE)
    assert headers == sorted(bundle.files)


def test_bundle_too_large_raises(profile, bundle):
    with pytest.raises(BundleTooLarge):
        build_source_bundle(profile, max_bytes=1_000)
    with pytest.raises(BundleTooLarge):
        build_source_bundle(profile, max_bytes=bundle.total_bytes - 1)
    assert build_source_bundle(profile, max_bytes=bundle.total_bytes).sha256 == bundle.sha256


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def test_build_messages_is_deterministic_and_shaped(profile, bundle, roles):
    first = build_messages(profile, bundle, roles)
    second = build_messages(profile, bundle, dict(reversed(list(roles.items()))))
    assert first == second
    assert [message["role"] for message in first] == ["system", "user"]
    user = first[1]["content"]
    assert "razorpay-vulnerable" in user
    assert '"order:A":"order_A"' in user
    assert "S9" in user and "duplicate_fulfilment" in user
    assert bundle.render() in user
    assert "ONE JSON object" in first[0]["content"]
    assert "at most 5" in first[0]["content"]


def test_build_messages_leaks_no_absolute_paths(profile, bundle, roles):
    text = json.dumps(build_messages(profile, bundle, roles))
    assert str(profile.target_dir) not in text
    assert str(profile.cwd) not in text
    assert str(Path.home()) not in text
    assert "/Users/" not in text


def test_prompt_fits_the_input_token_budget(profile, bundle, roles):
    text = "".join(message["content"] for message in build_messages(profile, bundle, roles))
    assert len(text) / 4 < limits.MODEL_INPUT_TOKENS


def test_prompt_example_matches_the_schema():
    output = parse_model_output(json.dumps(_EXAMPLE_OUTPUT))
    assert output.hypotheses[0].predicted_failure.check == "INV-1@1"


# --------------------------------------------------------------------------- #
# parse_model_output / repair_messages
# --------------------------------------------------------------------------- #
def _output_json(**hypothesis_overrides) -> str:
    hypothesis = json.loads(json.dumps(_EXAMPLE_OUTPUT["hypotheses"][0]))
    hypothesis.update(hypothesis_overrides)
    return json.dumps({"hypotheses": [hypothesis]})


def test_parse_valid_output():
    output = parse_model_output(_output_json())
    assert isinstance(output, ModelOutput)
    assert output.hypotheses[0].scenarios[0].template_id == "S3"


def test_parse_normalises_unversioned_check():
    text = _output_json(
        predicted_failure={"check": "INV-1", "kind": "duplicate_fulfilment", "role": "order:A"}
    )
    assert parse_model_output(text).hypotheses[0].predicted_failure.check == "INV-1@1"


def test_parse_accepts_empty_hypothesis_list():
    assert parse_model_output('{"hypotheses": []}').hypotheses == []


@pytest.mark.parametrize("fence", ["```json\n{body}\n```", "```\n{body}\n```", "  ```json\n{body}\n```  \n"])
def test_parse_strips_one_code_fence(fence):
    assert parse_model_output(fence.format(body=_output_json())).hypotheses


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("not json at all", "not valid JSON"),
        ('{"hypotheses": [', "not valid JSON"),
        ("[]", "valid dictionary"),
        ('{"hypotheses": [], "extra": 1}', "extra"),
        ("", "not valid JSON"),
    ],
)
def test_parse_rejects_bad_documents(text, fragment):
    with pytest.raises(OutputInvalid) as caught:
        parse_model_output(text)
    assert fragment in caught.value.errors
    assert isinstance(caught.value.errors, str)


def test_parse_rejects_extra_field_in_hypothesis():
    with pytest.raises(OutputInvalid) as caught:
        parse_model_output(_output_json(verdict="confirmed"))
    assert "verdict" in caught.value.errors


def test_parse_rejects_missing_field():
    hypothesis = json.loads(_output_json())["hypotheses"][0]
    del hypothesis["citations"]
    with pytest.raises(OutputInvalid) as caught:
        parse_model_output(json.dumps({"hypotheses": [hypothesis]}))
    assert "citations" in caught.value.errors


def test_parse_rejects_bad_enum_and_too_many_hypotheses():
    with pytest.raises(OutputInvalid):
        parse_model_output(_output_json(mechanism_kind="guess"))
    hypothesis = json.loads(_output_json())["hypotheses"][0]
    many = []
    for index in range(6):
        many.append({**hypothesis, "id": f"H{index}"})
    with pytest.raises(OutputInvalid):
        parse_model_output(json.dumps({"hypotheses": many}))


def test_parse_rejects_duplicate_ids():
    hypothesis = json.loads(_output_json())["hypotheses"][0]
    with pytest.raises(OutputInvalid) as caught:
        parse_model_output(json.dumps({"hypotheses": [hypothesis, hypothesis]}))
    assert "duplicate hypothesis ids: H1" in caught.value.errors


def test_repair_messages_structure_and_truncation():
    original = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    repaired = repair_messages(original, "x" * 5000, "e" * 3000)
    assert repaired[:2] == original
    assert repaired is not original and len(original) == 2
    assert [message["role"] for message in repaired] == ["system", "user", "assistant", "user"]
    assert repaired[2]["content"] == "x" * 4000
    assert "e" * 2000 in repaired[3]["content"]
    assert "e" * 2001 not in repaired[3]["content"]
    assert "JSON" in repaired[3]["content"]


# --------------------------------------------------------------------------- #
# Citations
# --------------------------------------------------------------------------- #
def test_citation_valid_with_real_quote(bundle):
    quote_line = _line_of(bundle, VULN, "The await below sits outside any transaction")
    citation = Citation(
        file=VULN,
        line_start=quote_line - 1,
        line_end=quote_line + 1,
        quote="The await below   sits outside any\n transaction.",
    )
    ok, errors = check_citations(_draft(citations=[citation]), bundle)
    assert ok and errors == []


def test_citation_valid_without_quote(bundle):
    ok, errors = check_citations(_draft(citations=[_vuln_citation(bundle)]), bundle)
    assert ok and errors == []


@pytest.mark.parametrize(
    "citation, fragment",
    [
        (Citation(file="razorpay_ref/nope.py", line_start=1, line_end=2), "not in the source bundle"),
        (Citation(file=VULN, line_start=1, line_end=10_000), "beyond"),
        (Citation(file=VULN, line_start=20, line_end=10), "after line_end"),
        (Citation(file=VULN, line_start=1, line_end=82), "exceeds 80"),
        (Citation(file=VULN, line_start=53, line_end=56, quote="this text is nowhere in the source"), "does not appear"),
        (Citation(file=VULN, line_start=53, line_end=56, quote="SELECT"), "shorter than 8"),
        (Citation(file=VULN, line_start=53, line_end=56, quote="   conn   "), "shorter than 8"),
    ],
)
def test_citation_invalid(bundle, citation, fragment):
    ok, errors = check_citations(_draft(citations=[citation]), bundle)
    assert not ok
    assert len(errors) == 1 and fragment in errors[0]


def test_citation_quote_must_be_inside_the_cited_lines(bundle):
    line = _line_of(bundle, VULN, "The await below sits outside any transaction")
    outside = Citation(
        file=VULN, line_start=line + 3, line_end=line + 4,
        quote="The await below sits outside any transaction.",
    )
    assert not check_citations(_draft(citations=[outside]), bundle)[0]


def test_citation_span_of_exactly_80_is_allowed(bundle):
    citation = Citation(file=VULN, line_start=1, line_end=80)
    assert check_citations(_draft(citations=[citation]), bundle)[0]


def test_all_citations_must_be_valid(bundle):
    good = _vuln_citation(bundle)
    bad = Citation(file="razorpay_ref/nope.py", line_start=1, line_end=1)
    ok, errors = check_citations(_draft(citations=[good, bad]), bundle)
    assert not ok and len(errors) == 1 and errors[0].startswith("citation[1]")


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_structure_passes_on_the_vulnerable_handler(bundle):
    status, detail = check_structure(_draft(citations=[_vuln_citation(bundle)]), bundle)
    assert status == "pass", detail
    assert "SELECT at line 53" in detail
    assert "await at line 74" in detail
    assert "INSERT at line 96" in detail


def test_structure_fails_on_the_fixed_handler(fixed_bundle):
    start = _line_of(fixed_bundle, FIXED, 'conn.execute("BEGIN IMMEDIATE")')
    end = _line_of(fixed_bundle, FIXED, "end transaction")
    draft = _draft(citations=[Citation(file=FIXED, line_start=start, line_end=end)])
    status, detail = check_structure(draft, fixed_bundle)
    assert status == "fail", detail
    assert "BEGIN" not in detail or "re-checks" in detail
    assert "re-checks state with a SELECT" in detail


def test_structure_not_applicable_for_other_mechanisms(bundle):
    draft = _draft(mechanism_kind="other", citations=[_vuln_citation(bundle)])
    assert check_structure(draft, bundle)[0] == "not_applicable"


def test_structure_fails_when_no_function_intersects(bundle):
    draft = _draft(citations=[Citation(file=VULN, line_start=1, line_end=3)])
    status, detail = check_structure(draft, bundle)
    assert status == "fail" and "no function intersects" in detail


def test_structure_fails_on_unparseable_file():
    bundle = SourceBundle(
        root=Path("."), files={"pkg/x.py": ["def broken(:", "  pass"]}, total_bytes=1, sha256="x"
    )
    draft = _draft(citations=[Citation(file="pkg/x.py", line_start=1, line_end=2)])
    status, detail = check_structure(draft, bundle)
    assert status == "fail" and "not parseable" in detail


def _synthetic(source: str) -> tuple[SourceBundle, HypothesisDraft]:
    lines = source.splitlines()
    bundle = SourceBundle(root=Path("."), files={"pkg/h.py": lines}, total_bytes=len(source), sha256="x")
    draft = _draft(citations=[Citation(file="pkg/h.py", line_start=1, line_end=len(lines))])
    return bundle, draft


RACY = '''
async def handle(conn, client):
    row = conn.execute("select status from t where id = ?", (1,)).fetchone()
    if row:
        return
    await client.get("/x")
    conn.execute("UPDATE t SET status = 'done' WHERE id = ?", (1,))
'''


def test_structure_synthetic_race_passes_and_is_case_insensitive():
    bundle, draft = _synthetic(RACY)
    assert check_structure(draft, bundle)[0] == "pass"


@pytest.mark.parametrize(
    "source",
    [
        # No await at all.
        RACY.replace("    await client.get(\"/x\")\n", ""),
        # Write precedes the read.
        '''
async def handle(conn, client):
    conn.execute("UPDATE t SET a = 1")
    await client.get("/x")
    conn.execute("SELECT 1")
''',
        # Await sits before the read.
        '''
async def handle(conn, client):
    await client.get("/x")
    conn.execute("SELECT 1")
    conn.execute("INSERT INTO t VALUES (1)")
''',
        # Non-literal SQL is not classified.
        '''
async def handle(conn, client, sql):
    conn.execute(sql)
    await client.get("/x")
    conn.execute(sql)
''',
    ],
)
def test_structure_synthetic_negative_orderings(source):
    bundle, draft = _synthetic(source)
    assert check_structure(draft, bundle)[0] == "fail"


def test_structure_recheck_before_write_defeats_the_race():
    source = RACY.replace(
        "    conn.execute(\"UPDATE",
        "    conn.execute(\"BEGIN EXCLUSIVE\")\n"
        "    conn.execute(\"SELECT status FROM t WHERE id = 1\")\n"
        "    conn.execute(\"UPDATE",
    )
    bundle, draft = _synthetic(source)
    assert check_structure(draft, bundle)[0] == "fail"


def test_structure_begin_without_recheck_select_still_races():
    source = RACY.replace(
        "    conn.execute(\"UPDATE",
        "    conn.execute(\"BEGIN IMMEDIATE\")\n    conn.execute(\"UPDATE",
    )
    bundle, draft = _synthetic(source)
    assert check_structure(draft, bundle)[0] == "pass"


# --------------------------------------------------------------------------- #
# Linkage
# --------------------------------------------------------------------------- #
def test_linkage_valid(roles, seed):
    ok, errors, built = check_linkage(_draft(), roles, seed)
    assert ok and errors == []
    assert [item.scenario_id for item in built] == [S3_ID]


def test_linkage_normalises_unversioned_check(roles, seed):
    draft = _draft(predicted_failure=PredictedFailure(check="INV-1", kind="duplicate_fulfilment", role="order:A"))
    assert check_linkage(draft, roles, seed)[0]


def test_linkage_accepts_expected_effect_check(roles, seed):
    draft = _draft(
        predicted_failure=PredictedFailure(
            check="expected-effect@1", kind="expected_effect_missing", role="payment:A1"
        )
    )
    assert check_linkage(draft, roles, seed)[0]


def test_linkage_unknown_check(roles, seed):
    draft = _draft(predicted_failure=PredictedFailure(check="INV-9@1", kind="duplicate_fulfilment", role="order:A"))
    ok, errors, _ = check_linkage(draft, roles, seed)
    assert not ok and any("unknown check" in error for error in errors)


def test_linkage_wrong_kind_for_check(roles, seed):
    draft = _draft(predicted_failure=PredictedFailure(check="INV-1@1", kind="amount_mismatch", role="order:A"))
    ok, errors, _ = check_linkage(draft, roles, seed)
    assert not ok and any("not valid for INV-1@1" in error for error in errors)


def test_linkage_unknown_role(roles, seed):
    draft = _draft(predicted_failure=PredictedFailure(check="INV-1@1", kind="duplicate_fulfilment", role="order:Z"))
    ok, errors, _ = check_linkage(draft, roles, seed)
    assert not ok and any("unknown role 'order:Z'" in error for error in errors)


def test_linkage_unknown_template(roles, seed):
    draft = _draft(scenarios=[ScenarioPick(template_id="S99")])
    ok, errors, built = check_linkage(draft, roles, seed)
    assert not ok and built == []
    assert any("scenario[0] S99" in error and "unknown template" in error for error in errors)


@pytest.mark.parametrize(
    "template, params, fragment",
    [
        ("S3", {"copies": 50}, "outside [2, 10]"),
        ("S3", {"copies": 1}, "outside [2, 10]"),
        ("S3", {"copies": "3"}, "must be an integer"),
        ("S3", {"copies": True}, "must be an integer"),
        ("S3", {"fault": "chaos"}, "must be one of"),
        ("S3", {"latency_ms": 9000}, "outside [0, 5000]"),
        ("S3", {"retries": 3}, "not allowed"),
        ("S5", {"copies": 3}, "not allowed"),
        ("S1", {"fault": "none"}, "not allowed"),
    ],
)
def test_linkage_bad_params(roles, seed, template, params, fragment):
    draft = _draft(scenarios=[ScenarioPick(template_id=template, params=params)])
    ok, errors, built = check_linkage(draft, roles, seed)
    assert not ok and built == []
    assert any(f"scenario[0] {template}" in error and fragment in error for error in errors)


def test_linkage_unlinked_scenario(roles, seed):
    draft = _draft(
        predicted_failure=PredictedFailure(check="INV-1@1", kind="duplicate_fulfilment", role="order:B"),
        scenarios=[ScenarioPick(template_id="S3")],
    )
    ok, errors, built = check_linkage(draft, roles, seed)
    assert not ok and built == []
    assert any("does not exercise role 'order:B'" in error for error in errors)


def test_linkage_links_only_scenarios_that_exercise_the_role(roles, seed):
    draft = _draft(
        predicted_failure=PredictedFailure(check="INV-1@1", kind="duplicate_fulfilment", role="order:B"),
        scenarios=[ScenarioPick(template_id="S3"), ScenarioPick(template_id="S8")],
    )
    ok, errors, built = check_linkage(draft, roles, seed)
    assert ok
    assert [item.scenario_id for item in built] == ["S8"]
    assert any("scenario[0] S3" in error for error in errors)


def test_linkage_dedupes_repeated_picks_and_keeps_buildable_ones(roles, seed):
    draft = _draft(
        scenarios=[
            ScenarioPick(template_id="S3", params={}),
            ScenarioPick(template_id="S3", params={"copies": 3, "fault": "rendezvous"}),
            ScenarioPick(template_id="S3", params={"copies": 99}),
            ScenarioPick(template_id="S2"),
        ]
    )
    ok, errors, built = check_linkage(draft, roles, seed)
    assert ok
    assert [item.scenario_id for item in built] == [S3_ID, "S2"]
    assert any("scenario[2] S3" in error for error in errors)


def test_scenario_roles_cover_deliveries_footprint_and_effects(roles, seed):
    s9 = build_scenario("S9", {}, roles, seed)
    assert scenario_roles(s9) == {"order:A", "payment:A1", "payment:A2"}
    s8 = build_scenario("S8", {}, roles, seed)
    assert {"order:A", "order:B", "payment:B1"} <= scenario_roles(s8)


# --------------------------------------------------------------------------- #
# assess / select_scenarios
# --------------------------------------------------------------------------- #
def test_assess_usable_hypothesis(bundle, roles, seed):
    hypothesis = _usable(bundle, roles, seed)
    assert hypothesis.status.citation == "valid"
    assert hypothesis.status.structure == "pass"
    assert hypothesis.status.linkage == "valid"
    assert hypothesis.status.outcome == "not_run"
    assert hypothesis.scenario_ids == [S3_ID]
    assert hypothesis.scenario_specs == [
        {"template_id": "S3", "params": {"copies": 3, "fault": "rendezvous", "latency_ms": 200}}
    ]
    assert hypothesis.source == "model"


def test_assess_unusable_when_citation_invalid(bundle, roles, seed):
    draft = _draft(citations=[Citation(file="razorpay_ref/nope.py", line_start=1, line_end=2)])
    hypothesis = assess(draft, bundle, roles, seed)
    assert not hypothesis.usable
    assert hypothesis.status.citation == "invalid"
    assert hypothesis.status.citation_errors


def test_assess_unusable_when_linkage_invalid(bundle, roles, seed):
    hypothesis = assess(
        _draft(citations=[_vuln_citation(bundle)], scenarios=[ScenarioPick(template_id="S99")]),
        bundle, roles, seed,
    )
    assert not hypothesis.usable
    assert hypothesis.status.citation == "valid"
    assert hypothesis.status.linkage == "invalid"


def test_structure_failure_alone_does_not_make_a_hypothesis_unusable(fixed_bundle, roles, seed):
    start = _line_of(fixed_bundle, FIXED, 'conn.execute("BEGIN IMMEDIATE")')
    end = _line_of(fixed_bundle, FIXED, "end transaction")
    draft = _draft(citations=[Citation(file=FIXED, line_start=start, line_end=end)])
    hypothesis = assess(draft, fixed_bundle, roles, seed)
    assert hypothesis.status.structure == "fail"
    assert hypothesis.usable  # plan C.12: usable = citation AND linkage


def test_select_scenarios_controls_first_and_nothing_else_without_hypotheses():
    assert select_scenarios([]) == [ScenarioSpec("S1", {}), ScenarioSpec("S2", {})]


def test_select_scenarios_dedupes_caps_and_skips_unusable(bundle, roles, seed):
    first = _usable(bundle, roles, seed, scenarios=[ScenarioPick(template_id="S1"), ScenarioPick(template_id="S3")])
    second = _usable(
        bundle, roles, seed,
        id="H2",
        scenarios=[
            ScenarioPick(template_id="S3", params={"copies": 3, "fault": "rendezvous", "latency_ms": 200}),
            ScenarioPick(template_id="S5"),
            ScenarioPick(template_id="S6"),
            ScenarioPick(template_id="S9"),
        ],
    )
    bad = assess(
        _draft(id="H3", citations=[Citation(file="nope.py", line_start=1, line_end=1)],
               scenarios=[ScenarioPick(template_id="S8")]),
        bundle, roles, seed,
    )
    assert not bad.usable

    selected = select_scenarios([first, bad, second])
    ids = [spec.scenario_id for spec in selected]
    assert ids == [
        "S1", "S2", S3_ID,
        "S5[fault=rendezvous,latency_ms=200]", "S6", "S9",
    ]
    assert "S8" not in ids
    assert len(ids) == len(set(ids))

    capped = select_scenarios([first, second], max_total=4)
    assert [spec.scenario_id for spec in capped] == ids[:4]
    assert all(isinstance(spec, ScenarioSpec) for spec in selected)
    assert selected[2] == ScenarioSpec("S3", {"copies": 3, "fault": "rendezvous", "latency_ms": 200})


def test_selected_specs_build(bundle, roles, seed):
    hypothesis = _usable(bundle, roles, seed)
    for spec in select_scenarios([hypothesis]):
        assert spec.build(roles, seed).scenario_id == spec.scenario_id


# --------------------------------------------------------------------------- #
# resolve_outcome
# --------------------------------------------------------------------------- #
def test_outcome_confirmed(bundle, roles, seed):
    hypothesis = _usable(bundle, roles, seed)
    results = {
        S3_ID: _result(
            S3_ID, "FAIL",
            [_verdict("PASS"), _verdict("FAIL", [_violation()]), _verdict("FAIL", [_violation()])],
            fail=2,
        )
    }
    resolved = resolve_outcome(hypothesis, results)
    assert resolved.status.outcome == "confirmed"
    assert S3_ID in resolved.status.outcome_detail and "2/3" in resolved.status.outcome_detail
    assert hypothesis.status.outcome == "not_run"  # original is untouched
    assert resolved.draft == hypothesis.draft and resolved.scenario_ids == hypothesis.scenario_ids


def test_outcome_confirmed_via_expected_effect_miss(bundle, roles, seed):
    hypothesis = _usable(
        bundle, roles, seed,
        predicted_failure=PredictedFailure(
            check="expected-effect", kind="expected_effect_missing", role="payment:A1"
        ),
    )
    miss = _violation("expected-effect@1", "expected_effect_missing", ("payment:A1", "order:A"))
    results = {S3_ID: _result(S3_ID, "FAIL", [_verdict("FAIL", misses=[miss])], fail=1)}
    assert resolve_outcome(hypothesis, results).status.outcome == "confirmed"


@pytest.mark.parametrize(
    "violation",
    [
        _violation(check="INV-2@1", kind="missing_reference"),
        _violation(kind="duplicate_ack_inconsistent"),
        _violation(roles=("order:B",)),
    ],
)
def test_outcome_different_failure_when_prediction_does_not_match(bundle, roles, seed, violation):
    hypothesis = _usable(bundle, roles, seed)
    signature = FailureSignature(check=violation.check, kind=violation.kind, roles=violation.roles)
    results = {S3_ID: _result(S3_ID, "FAIL", [_verdict("FAIL", [violation])], fail=1, signature=signature)}
    resolved = resolve_outcome(hypothesis, results)
    assert resolved.status.outcome == "different_failure"
    assert S3_ID in resolved.status.outcome_detail


def test_outcome_matching_violation_on_a_non_fail_trial_does_not_confirm(bundle, roles, seed):
    hypothesis = _usable(bundle, roles, seed)
    results = {S3_ID: _result(S3_ID, "PASS", [_verdict("PASS", [_violation()])])}
    assert resolve_outcome(hypothesis, results).status.outcome == "refuted"


def test_outcome_refuted_when_every_linked_scenario_passes(bundle, roles, seed):
    hypothesis = _usable(
        bundle, roles, seed,
        scenarios=[ScenarioPick(template_id="S3"), ScenarioPick(template_id="S2")],
    )
    results = {
        S3_ID: _result(S3_ID, "PASS", [_verdict("PASS")] * 5),
        "S2": _result("S2", "PASS", [_verdict("PASS")] * 5),
    }
    resolved = resolve_outcome(hypothesis, results)
    assert resolved.status.outcome == "refuted"
    assert "S2" in resolved.status.outcome_detail


def test_outcome_inconclusive(bundle, roles, seed):
    hypothesis = _usable(
        bundle, roles, seed,
        scenarios=[ScenarioPick(template_id="S3"), ScenarioPick(template_id="S2")],
    )
    results = {
        S3_ID: _result(S3_ID, "PASS", [_verdict("PASS")] * 5),
        "S2": _result("S2", "INCONCLUSIVE", [_verdict("INCONCLUSIVE")], valid=0),
    }
    resolved = resolve_outcome(hypothesis, results)
    assert resolved.status.outcome == "inconclusive"
    assert "S2" in resolved.status.outcome_detail


def test_outcome_not_run_without_results_or_when_unusable(bundle, roles, seed):
    hypothesis = _usable(bundle, roles, seed)
    assert resolve_outcome(hypothesis, {}).status.outcome == "not_run"
    assert resolve_outcome(hypothesis, {"S1": _result("S1", "PASS")}).status.outcome == "not_run"

    unusable = assess(
        _draft(citations=[Citation(file="nope.py", line_start=1, line_end=1)]), bundle, roles, seed
    )
    results = {S3_ID: _result(S3_ID, "FAIL", [_verdict("FAIL", [_violation()])], fail=1)}
    assert resolve_outcome(unusable, results).status.outcome == "not_run"


def test_outcome_only_considers_the_hypotheses_own_scenarios(bundle, roles, seed):
    hypothesis = _usable(bundle, roles, seed)
    results = {
        S3_ID: _result(S3_ID, "PASS", [_verdict("PASS")] * 5),
        "S8": _result("S8", "FAIL", [_verdict("FAIL", [_violation()])], fail=1),
    }
    assert resolve_outcome(hypothesis, results).status.outcome == "refuted"


# --------------------------------------------------------------------------- #
# template_explanation
# --------------------------------------------------------------------------- #
def test_template_explanation_is_data_only_and_deterministic():
    signature = FailureSignature(check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"])
    results = [
        _result("S1", "PASS", [_verdict("PASS")] * 5),
        _result(S3_ID, "FAIL", [_verdict("FAIL", [_violation()])] * 4, valid=5, fail=4, signature=signature),
        _result("S6", "INCONCLUSIVE", [_verdict("INCONCLUSIVE")], valid=0),
        _result("S2", "PASS", [_verdict("PASS")] * 5),
    ]
    text = template_explanation(results)
    assert text == template_explanation(results)
    assert text.splitlines() == [
        f"{S3_ID} failed: INV-1@1 duplicate_fulfilment on order:A in 4/5 valid trials.",
        "2 scenarios passed, 1 inconclusive, 1 failed of 4 run.",
    ]


def test_template_explanation_without_failures_or_signature():
    assert template_explanation([]) == "0 scenarios passed, 0 inconclusive, 0 failed of 0 run."
    text = template_explanation([_result("S1", "FAIL", fail=1)])
    assert text.splitlines()[0] == "S1 failed: no signature in 1/5 valid trials."


# --------------------------------------------------------------------------- #
# Closed vocabularies
# --------------------------------------------------------------------------- #
def test_known_kinds_match_the_invariant_pack_source():
    pack = (REPO / "src/duress/invariants/payments_ref_v1.py").read_text()
    effects = (REPO / "src/duress/expectations.py").read_text()
    literal_kinds: dict[str, set[str]] = {}
    for check, kind in re.findall(r'check="([^"]+)",\s*kind="([^"]+)"', pack):
        literal_kinds.setdefault(check, set()).add(kind)
    for check, kinds in KNOWN_KINDS.items():
        source = effects if check.startswith("expected-effect") else pack
        assert f'"{check}"' in source or check.startswith("expected-effect"), check
        for kind in kinds:
            assert f'"{kind}"' in source, f"{kind} missing from the source for {check}"
    # And nothing the pack emits is missing from the investigator's vocabulary.
    for check, kinds in literal_kinds.items():
        assert kinds <= set(KNOWN_KINDS[check]), check


def test_library_covers_every_template_and_matches_the_registry(roles, seed):
    assert [entry["id"] for entry in SCENARIO_LIBRARY] == list(TEMPLATES)
    for spec in EXHAUSTIVE_SPECS:
        assert spec.template_id in {entry["id"] for entry in SCENARIO_LIBRARY}
    assert len(EXHAUSTIVE_SET) == 7
    for entry in SCENARIO_LIBRARY:
        built = build_scenario(entry["id"], {}, roles, seed)
        assert sorted(entry["roles"]) == sorted(scenario_roles(built)), entry["id"]
        assert entry["summary"] and entry["exercises"]


def test_library_param_bounds_agree_with_the_builders(roles, seed):
    s3 = {entry["id"]: entry for entry in SCENARIO_LIBRARY}["S3"]["params"]
    assert (s3["copies"]["min"], s3["copies"]["max"]) == (2, 10)
    for copies in (2, 10):
        assert not validate_params("S3", {"copies": copies})
        build_scenario("S3", {"copies": copies}, roles, seed)
    for copies in (1, 11):
        assert validate_params("S3", {"copies": copies})
        with pytest.raises(ValueError):
            build_scenario("S3", {"copies": copies}, roles, seed)
    for fault in s3["fault"]["values"]:
        assert not validate_params("S5", {"fault": fault, "latency_ms": 5000})
        build_scenario("S5", {"fault": fault, "latency_ms": 5000}, roles, seed)


def test_library_is_static_and_json_serialisable():
    assert json.dumps(SCENARIO_LIBRARY, sort_keys=True) == json.dumps(SCENARIO_LIBRARY, sort_keys=True)
    json.loads(json.dumps(SCENARIO_LIBRARY))


def test_outcome_cannot_refute_when_a_linked_scenario_was_not_executed(bundle, roles, seed):
    hypothesis = _usable(
        bundle, roles, seed,
        scenarios=[ScenarioPick(template_id="S3"), ScenarioPick(template_id="S5")],
    )
    results = {S3_ID: _result(S3_ID, "PASS", [_verdict("PASS")] * 5)}
    resolved = resolve_outcome(hypothesis, results)
    assert resolved.status.outcome == "inconclusive"
    assert hypothesis.scenario_ids[1] in resolved.status.outcome_detail
