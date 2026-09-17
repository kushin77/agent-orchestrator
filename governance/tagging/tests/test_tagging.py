"""Suite for the tag authority (issue #1175).

The suite's discipline is the repository's own: every refusal is driven with a
**clean twin**, so a rule that fires on everything is caught here rather than
passing as a strict gate. A test that only plants a bad input proves the rule can
fail; a test that also feeds the good input proves the rule can PASS, which is
what makes the gate an enforcement rather than a wall.

Run directly (the `check-tagging` gate runs it):

    python3 -m pytest governance/tagging/tests -q
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "governance" / "tagging"))

import e2e as E  # noqa: E402
import ledger as L  # noqa: E402
import live as LV  # noqa: E402
import model as M  # noqa: E402
import policy as P  # noqa: E402
import schema as S  # noqa: E402

TAXONOMY = ROOT / "governance" / "tagging" / "taxonomy.yaml"
RULES = ROOT / "governance" / "tagging" / "rules.yaml"
CONTROLS = ROOT / "governance" / "tagging" / "controls.yaml"


@pytest.fixture(scope="module")
def taxonomy() -> M.Taxonomy:
    return M.load_taxonomy(TAXONOMY)


@pytest.fixture(scope="module")
def rules() -> M.Rules:
    return M.load_rules(RULES)


def codes(findings) -> set[str]:
    return {f.code for f in findings}


# -- the authority is loadable and clean --------------------------------------
def test_taxonomy_declares_its_schema(taxonomy):
    assert taxonomy.dimensions
    assert "class" in taxonomy.dimensions
    assert taxonomy.dimensions["class"].kind == M.KIND_BORROWED


def test_taxonomy_is_self_consistent(taxonomy):
    assert M.errors(M.lint_taxonomy(taxonomy)) == ()


def test_rules_are_self_consistent(rules, taxonomy):
    assert M.errors(M.lint_rules(rules, taxonomy)) == ()


def test_every_rule_gate_resolves(rules):
    assert M.errors(M.lint_gates(rules, ROOT)) == ()


def test_borrowed_vocabularies_do_not_drift(taxonomy):
    assert M.errors(M.drift(taxonomy, ROOT)) == ()


def test_finops_rank_ladder_agrees_with_the_declaration():
    declared, findings = M.finops_rank_map(RULES)
    assert M.errors(findings) == ()
    assert declared == M.FINOPS_RANK


# -- the refusal set is not a formality ---------------------------------------
def test_every_declared_refusal_is_a_raised_code(taxonomy):
    assert set(taxonomy.refusal_ids) == set(M.REFUSAL_CODES)


# -- drift is genuinely fail-able (the mutant is refused, the clean tree is not)
def test_drift_refuses_a_minted_rung(tmp_path, taxonomy):
    """A taxonomy that mints a rung the authority does not carry is refused."""
    text = TAXONOMY.read_text(encoding="utf-8")
    mutant = text.replace(
        "values: [template, class, pattern, enterprise, faang, elite]",
        "values: [template, class, pattern, enterprise, faang, elite, mythic]",
    )
    assert mutant != text, "the mutation did not land"
    path = tmp_path / "taxonomy.yaml"
    path.write_text(mutant, encoding="utf-8")
    findings = M.drift(M.load_taxonomy(path), ROOT)
    assert M.CODE_VOCABULARY_DRIFT in codes(findings)
    assert any("mythic" in f.message for f in findings)


def test_drift_refuses_a_retired_rung(tmp_path):
    """Dropping a rung is drift too — a subset is not a lazy match."""
    text = TAXONOMY.read_text(encoding="utf-8")
    mutant = text.replace(
        "values: [template, class, pattern, enterprise, faang, elite]",
        "values: [enterprise, faang, elite]",
    )
    assert mutant != text
    path = tmp_path / "taxonomy.yaml"
    path.write_text(mutant, encoding="utf-8")
    findings = M.drift(M.load_taxonomy(path), ROOT)
    assert M.CODE_VOCABULARY_DRIFT in codes(findings)


def test_name_authority_refuses_a_retired_label_name(tmp_path):
    """A dimension whose label name policy.yaml no longer declares is refused."""
    text = TAXONOMY.read_text(encoding="utf-8")
    mutant = text.replace("  priority:\n", "  urgency:\n", 1).replace(
        "    name_authority:\n      path: governance/conformance/policy.yaml\n      pointer: required\n    values: [P0, P1, P2, P3]",
        "    name_authority:\n      path: governance/conformance/policy.yaml\n      pointer: required\n    values: [P0, P1, P2, P3]",
    )
    path = tmp_path / "taxonomy.yaml"
    path.write_text(mutant, encoding="utf-8")
    findings = M.drift(M.load_taxonomy(path), ROOT)
    assert M.CODE_NAME_AUTHORITY_DRIFT in codes(findings)


def test_unknown_gate_is_refused(tmp_path, taxonomy):
    """A rule naming a gate that does not exist is refused by name."""
    text = RULES.read_text(encoding="utf-8")
    mutant = text.replace("make:tf-fmt", "make:tf-fmt-renamed", 1)
    assert mutant != text
    path = tmp_path / "rules.yaml"
    path.write_text(mutant, encoding="utf-8")
    findings = M.lint_gates(M.load_rules(path), ROOT)
    assert M.CODE_UNKNOWN_GATE in codes(findings)
    assert any("tf-fmt-renamed" in f.message for f in findings)


def test_unknown_channel_is_refused(tmp_path):
    text = RULES.read_text(encoding="utf-8")
    mutant = text.replace("{gate: make:tf-fmt, channel: ci}", "{gate: make:tf-fmt, channel: someday}")
    path = tmp_path / "rules.yaml"
    path.write_text(mutant, encoding="utf-8")
    findings = M.lint_rules(M.load_rules(path), M.load_taxonomy(TAXONOMY))
    assert M.CODE_RULES_INVALID in codes(findings)


def test_rule_on_an_undeclared_dimension_is_refused(tmp_path, taxonomy):
    text = RULES.read_text(encoding="utf-8")
    mutant = text.replace("dimension: posture", "dimension: mood", 1)
    assert mutant != text
    path = tmp_path / "rules.yaml"
    path.write_text(mutant, encoding="utf-8")
    findings = M.lint_rules(M.load_rules(path), taxonomy)
    assert M.CODE_RULE_UNKNOWN_DIMENSION in codes(findings)


# -- tag-set judgement: each refusal with its clean twin ----------------------
CLEAN = [
    "class:enterprise",
    "type:feature",
    "priority:P1",
    "area:standards",
    "posture:iac",
    "lifecycle:build",
]


def test_a_clean_tag_set_is_refused_nothing(taxonomy, rules):
    findings = M.validate_tags(M.parse_labels(CLEAN), taxonomy, target="issue")
    assert M.errors(findings) == ()


def test_unknown_dimension_is_refused(taxonomy):
    tags = M.parse_labels(CLEAN + ["vibe:elite"])
    findings = M.validate_tags(tags, taxonomy)
    assert M.CODE_UNKNOWN_DIMENSION in codes(findings)


def test_unknown_value_is_refused(taxonomy):
    tags = M.parse_labels(
        [t for t in CLEAN if not t.startswith("posture:")] + ["posture:magic"]
    )
    findings = M.validate_tags(tags, taxonomy)
    assert M.CODE_UNKNOWN_VALUE in codes(findings)


def test_pattern_dimension_refuses_a_bad_shape(taxonomy):
    tags = M.parse_labels([t for t in CLEAN if not t.startswith("area:")] + ["area:Bad_Area"])
    findings = M.validate_tags(tags, taxonomy)
    assert M.CODE_VALUE_PATTERN in codes(findings)


def test_pattern_dimension_accepts_a_good_shape(taxonomy):
    tags = M.parse_labels([t for t in CLEAN if not t.startswith("area:")] + ["area:erp-module"])
    findings = M.validate_tags(tags, taxonomy)
    assert M.errors(findings) == ()


def test_mutually_exclusive_postures_are_refused(taxonomy):
    tags = M.parse_labels(
        [t for t in CLEAN if not t.startswith("posture:")]
        + ["posture:no-human-needed", "posture:human-gated"]
    )
    findings = M.validate_tags(tags, taxonomy)
    assert M.CODE_POSTURE_CONTRADICTION in codes(findings)


def test_each_autonomy_posture_alone_is_accepted(taxonomy):
    for posture in ("no-human-needed", "human-gated"):
        tags = M.parse_labels(
            [t for t in CLEAN if not t.startswith("posture:")] + ["posture:%s" % posture]
        )
        assert M.errors(M.validate_tags(tags, taxonomy)) == ()


def test_single_valued_dimension_refuses_two_values(taxonomy):
    tags = M.parse_labels(CLEAN + ["lifecycle:release"])
    findings = M.validate_tags(tags, taxonomy)
    assert M.CODE_UNKNOWN_VALUE in codes(findings)


def test_multi_valued_posture_accepts_two_values(taxonomy):
    tags = M.parse_labels(CLEAN + ["posture:saas", "posture:overall"])
    assert M.errors(M.validate_tags(tags, taxonomy)) == ()


def test_required_missing_is_refused(taxonomy):
    tags = M.parse_labels(["class:enterprise"])
    findings = M.validate_tags(tags, taxonomy, target="issue")
    assert M.CODE_REQUIRED_MISSING in codes(findings)
    assert len([f for f in findings if f.code == M.CODE_REQUIRED_MISSING]) >= 3


def test_recommended_is_a_deviation_and_escalates_under_strict(taxonomy):
    tags = M.parse_labels([t for t in CLEAN if not t.startswith("lifecycle:")])
    relaxed = M.validate_tags(tags, taxonomy, strict=False)
    strict = M.validate_tags(tags, taxonomy, strict=True)
    assert M.errors(relaxed) == ()
    assert M.CODE_REQUIRED_MISSING in codes(M.errors(strict))


def test_unknown_target_is_refused(taxonomy):
    tags = M.parse_labels(["posture:iac"])
    findings = M.validate_tags(tags, taxonomy, target="surface")
    assert M.CODE_UNKNOWN_TARGET in codes(findings)


def test_untagged_labels_are_ignored_not_guessed(taxonomy):
    tags = M.parse_labels(["bug", "help wanted", "class:elite"])
    assert tags.values == {"class": ("elite",)}
    assert M.CODE_UNKNOWN_DIMENSION not in codes(M.validate_tags(tags, taxonomy))


# -- the derivation is the end-to-end half ------------------------------------
def test_plan_channels_are_derived_from_the_tags(taxonomy, rules):
    plan, findings = M.derive(M.parse_labels(CLEAN), taxonomy, rules)
    assert set(plan.gates).issubset(set(M.CHANNELS))
    assert "ci" in plan.gates and "pr" in plan.gates
    assert "make:terraform" in plan.gates["ci"]
    assert "posture-iac" in plan.rules_fired
    assert M.errors(findings) == ()


def test_plan_raises_the_floor_for_iac(taxonomy, rules):
    plan, _ = M.derive(M.parse_labels(CLEAN), taxonomy, rules)
    assert plan.finops_floor == "pro"
    assert "flag-gated-off" in plan.declarations


def test_plan_refuses_a_tier_below_the_floor(taxonomy, rules):
    tags = M.parse_labels(CLEAN + ["finops:flash"])
    _, findings = M.derive(tags, taxonomy, rules)
    assert M.CODE_FINOPS_FLOOR_UNMET in codes(findings)


def test_plan_accepts_a_tier_at_the_floor(taxonomy, rules):
    tags = M.parse_labels(CLEAN + ["finops:pro"])
    _, findings = M.derive(tags, taxonomy, rules)
    assert M.errors(findings) == ()


def test_plan_forbids_an_escalation_marker_for_autonomy(taxonomy, rules):
    tags = M.parse_labels(
        [t for t in CLEAN if not t.startswith("posture:")] + ["posture:no-human-needed"]
    )
    plan, _ = M.derive(tags, taxonomy, rules)
    assert "escalation" in plan.forbids


def test_release_lifecycle_pulls_in_the_cd_gates(taxonomy, rules):
    tags = M.parse_labels(
        [t for t in CLEAN if not t.startswith("lifecycle:")] + ["lifecycle:release"]
    )
    plan, _ = M.derive(tags, taxonomy, rules)
    assert "cd" in plan.gates
    assert "make:feature-flags" in plan.gates["cd"]


def test_class_at_least_fires_the_higher_rungs_too(taxonomy, rules):
    tags = M.parse_labels(
        [t for t in CLEAN if not t.startswith("class:")] + ["class:elite", "finops:pro"]
    )
    plan, _ = M.derive(tags, taxonomy, rules)
    for rule_id in ("class-enterprise", "class-faang", "class-elite"):
        assert rule_id in plan.rules_fired


# -- the matrix is generated, not written -------------------------------------
def test_matrix_is_deterministic(taxonomy, rules):
    assert M.render_matrix(taxonomy, rules) == M.render_matrix(taxonomy, rules)


def test_matrix_names_every_rule(taxonomy, rules):
    rendered = M.render_matrix(taxonomy, rules)
    for rule in rules.rules:
        assert "`%s`" % rule.id in rendered


def test_matrix_block_is_extractable():
    text = "head\n%s\nmid\n%s\ntail" % (M.MATRIX_BEGIN, M.MATRIX_END)
    block = M.extract_matrix(text)
    assert block is not None
    assert block.startswith(M.MATRIX_BEGIN)
    assert block.endswith(M.MATRIX_END)


def test_extract_matrix_returns_none_when_absent():
    assert M.extract_matrix("no markers here") is None


# -- the loader refuses rather than guessing ---------------------------------
def test_missing_file_is_unavailable(tmp_path):
    with pytest.raises(M.TaggingUnavailable):
        M.load_taxonomy(tmp_path / "nope.yaml")


def test_wrong_schema_is_unavailable(tmp_path, taxonomy):
    path = tmp_path / "taxonomy.yaml"
    path.write_text("schema: something/else\n", encoding="utf-8")
    with pytest.raises(M.TaggingUnavailable):
        M.load_taxonomy(path)


def test_pointer_resolution_is_total(taxonomy):
    with pytest.raises(KeyError):
        M.resolve_pointer({"a": {"b": 1}}, "a.c")


def test_pointer_resolution_reads_nested_lists():
    assert M.resolve_pointer({"a": {"b": ["x", "y"]}}, "a.b") == ["x", "y"]


# ---------------------------------------------------------------------------
# the frozen shapes, the ledger and the live projection (issue #1175)
# ---------------------------------------------------------------------------
@pytest.fixture()
def controls() -> dict:
    return P.load(CONTROLS)


def test_schema_declares_the_four_shapes():
    assert set(S.shapes()) == {"taxonomy", "rules", "controls", "ledger_row"}


def test_the_authority_satisfies_its_own_frozen_shapes():
    for shape, path in (("taxonomy", TAXONOMY), ("rules", RULES), ("controls", CONTROLS)):
        document = M._load_doc(path)
        assert S.problems(document, shape) == (), "%s violates %s" % (path.name, shape)


def test_an_unknown_shape_is_refused():
    with pytest.raises(S.SchemaUnavailable):
        S.problems({}, "nonsense")


def test_the_schema_refuses_a_shape_it_cannot_measure(tmp_path):
    """A schema using an unimplemented keyword is refused, not silently ignored."""
    broken = tmp_path / "broken.schema.json"
    broken.write_text(
        json.dumps({"type": "object", "properties": {"x": {"type": "string", "maxLength": 3}}}),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        S.problems({"x": "abcd"}, "taxonomy", path=broken)


def test_controls_agree_with_the_authority(taxonomy, rules, controls):
    assert M.errors(P.check(taxonomy, rules, controls, rules_path=RULES)) == ()


def test_controls_refuse_a_relaxed_required_set(tmp_path, taxonomy, rules):
    mutant = tmp_path / "controls.yaml"
    text = CONTROLS.read_text(encoding="utf-8")
    mutant.write_text(
        text.replace("issue: [class, type, priority, area]", "issue: [class]"),
        encoding="utf-8",
    )
    findings = P.check(taxonomy, rules, P.load(mutant), rules_path=RULES)
    assert P.CODE_REQUIRED_MISMATCH in {f.code for f in findings}


def test_controls_refuse_a_downgraded_severity(tmp_path, taxonomy, rules):
    mutant = tmp_path / "controls.yaml"
    text = CONTROLS.read_text(encoding="utf-8")
    # Target the refusal-severity control by its id, not by the first
    # `severity:` line in the file — the other controls are errors too.
    marker = "  - id: refusal-severity"
    head, _, tail = text.partition(marker)
    assert tail, "the refusal-severity control is absent from controls.yaml"
    mutated_tail = tail.replace("severity: error", "severity: warning", 1)
    assert mutated_tail != tail, "the severity mutation did not land"
    mutant.write_text(head + marker + mutated_tail, encoding="utf-8")
    findings = P.check(taxonomy, rules, P.load(mutant), rules_path=RULES)
    assert P.CODE_SEVERITY_MISMATCH in {f.code for f in findings}
    findings = P.check(taxonomy, rules, P.load(mutant), rules_path=RULES)
    assert P.CODE_SEVERITY_MISMATCH in {f.code for f in findings}


def test_controls_refuse_a_drifted_finops_ladder(tmp_path, taxonomy, rules):
    mutant = tmp_path / "controls.yaml"
    text = CONTROLS.read_text(encoding="utf-8")
    mutant.write_text(text.replace("auditor: 3", "auditor: 9"), encoding="utf-8")
    findings = P.check(taxonomy, rules, P.load(mutant), rules_path=RULES)
    assert P.CODE_FLOOR_MISMATCH in {f.code for f in findings}


def test_controls_refuse_a_limit_the_authority_has_crossed(tmp_path, taxonomy, rules):
    mutant = tmp_path / "controls.yaml"
    text = CONTROLS.read_text(encoding="utf-8")
    mutant.write_text(text.replace("max_dimensions: 32", "max_dimensions: 1"), encoding="utf-8")
    findings = P.check(taxonomy, rules, P.load(mutant), rules_path=RULES)
    assert P.CODE_LIMIT_EXCEEDED in {f.code for f in findings}


def test_a_wrong_controls_schema_is_unavailable(tmp_path):
    path = tmp_path / "controls.yaml"
    path.write_text("schema: something/else\ncontrols: []\n", encoding="utf-8")
    with pytest.raises(P.ControlsUnavailable):
        P.load(path)


# -- the ledger is a real audit trail ----------------------------------------
def test_a_row_round_trips(tmp_path):
    path = tmp_path / "ledger.jsonl"
    L.append(L.row("plan", "subject", "ok", exit_code=0, tags=["class:elite"]), path)
    rows, malformed = L.read(path)
    assert malformed == ()
    assert len(rows) == 1
    assert rows[0]["tags"] == ["class:elite"]


def test_the_ledger_appends_and_never_rewrites(tmp_path):
    path = tmp_path / "ledger.jsonl"
    L.append(L.row("plan", "one", "ok", exit_code=0), path)
    L.append(L.row("check", "two", "refused", exit_code=1), path)
    rows, _ = L.read(path)
    assert [r["subject"] for r in rows] == ["one", "two"]


@pytest.mark.parametrize(
    "bad",
    [
        {"at": "x", "kind": "plan", "subject": "s", "decision": "maybe"},
        {"at": "x", "kind": "nope", "subject": "s", "decision": "ok"},
        {"at": "x", "kind": "plan", "subject": "   ", "decision": "ok"},
        {"at": "x", "kind": "plan", "subject": "s", "decision": "ok", "undeclared": 1},
        {"at": "x", "kind": "plan", "subject": "s"},
    ],
)
def test_the_ledger_refuses_a_malformed_row_before_the_write(tmp_path, bad):
    path = tmp_path / "ledger.jsonl"
    with pytest.raises(L.LedgerRefused):
        L.append(bad, path)
    assert not path.exists(), "a refused row must not be written"


def test_the_ledger_refuses_an_illegal_kind_at_construction():
    with pytest.raises(L.LedgerRefused):
        L.row("nonsense", "s", "ok")


def test_the_ledger_reads_past_a_garbage_line(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        "garbage\n" + json.dumps(L.row("plan", "good", "ok", exit_code=0)) + "\n",
        encoding="utf-8",
    )
    rows, malformed = L.read(path)
    assert len(rows) == 1
    assert len(malformed) == 1 and "line 1" in malformed[0]


def test_an_absent_ledger_reads_empty(tmp_path):
    rows, malformed = L.read(tmp_path / "absent.jsonl")
    assert rows == () and malformed == ()


def test_the_ledger_summary_counts_by_kind(tmp_path):
    path = tmp_path / "ledger.jsonl"
    L.append(L.row("plan", "a", "ok", exit_code=0), path)
    L.append(L.row("plan", "b", "refused", exit_code=1), path)
    summary = L.summarise(path)
    assert summary["by_kind"] == {"plan": 2}
    assert summary["by_decision"] == {"ok": 1, "refused": 1}
    assert summary["malformed"] == []


def test_a_ledger_write_failure_does_not_raise(tmp_path, capsys):
    """An unwritable ledger reports; it does not take the verb down with it."""
    unwritable = tmp_path / "a-file" / "ledger.jsonl"
    (tmp_path / "a-file").write_text("not a directory", encoding="utf-8")
    assert L.record("plan", "s", "ok", path=unwritable) is None
    assert "could not record" in capsys.readouterr().err


# -- the live projection reports what it actually read -----------------------
def _stamp(minutes_ago: float = 0.0) -> str:
    """A snapshot timestamp DERIVED from the live clock, never a literal date.

    RCA-0008 (#506) is the trap this avoids: a fixture seeded with a literal date
    while the code resolves its bucket from the live clock is green on the day it
    is written and red every day after.
    """
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot(tmp_path: Path, issues, minutes_ago: float = 0.0) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"generated_at": _stamp(minutes_ago), "issues": list(issues)}),
        encoding="utf-8",
    )
    return path


GOOD_ISSUE = {
    "number": 1,
    "state": "open",
    "labels": [
        {"name": "class:enterprise"},
        {"name": "type:feature"},
        {"name": "priority:P1"},
        {"name": "area:standards"},
    ],
}


def test_the_projection_counts_coverage(tmp_path, taxonomy):
    issues = [
        GOOD_ISSUE,
        {"number": 2, "state": "open", "labels": []},
        {"number": 3, "state": "closed", "labels": [{"name": "posture:magic"}]},
    ]
    projection = LV.project(ROOT, taxonomy, snapshot_path=_snapshot(tmp_path, issues))
    assert projection["open_issues"] == 2
    assert projection["tagged"] == 1
    assert projection["untagged"] == 1


def test_the_projection_names_the_item_it_refuses(tmp_path, taxonomy):
    issues = [
        GOOD_ISSUE,
        {
            "number": 7,
            "state": "open",
            "labels": [
                {"name": "posture:no-human-needed"},
                {"name": "posture:human-gated"},
            ],
        },
    ]
    projection = LV.project(ROOT, taxonomy, snapshot_path=_snapshot(tmp_path, issues))
    assert "posture-contradiction" in projection["refusals"]
    assert any("issue-7" in message for message in projection["refusals"]["posture-contradiction"])


def test_the_projection_reports_an_undeclared_dimension_in_use(tmp_path, taxonomy):
    issues = [dict(GOOD_ISSUE, labels=GOOD_ISSUE["labels"] + [{"name": "vibe:elite"}])]
    projection = LV.project(ROOT, taxonomy, snapshot_path=_snapshot(tmp_path, issues))
    assert "vibe" in projection["dimensions_undeclared_but_used"]


def test_the_projection_reports_a_declared_but_unused_dimension(tmp_path, taxonomy):
    projection = LV.project(ROOT, taxonomy, snapshot_path=_snapshot(tmp_path, [GOOD_ISSUE]))
    assert "lifecycle" in projection["dimensions_declared_but_unused"]


def test_the_projection_accepts_a_clean_board(tmp_path, taxonomy):
    projection = LV.project(ROOT, taxonomy, snapshot_path=_snapshot(tmp_path, [GOOD_ISSUE]))
    assert projection["refusal_count"] == 0


def test_the_projection_is_unavailable_without_a_snapshot(tmp_path, taxonomy):
    with pytest.raises(LV.LiveUnavailable):
        LV.project(ROOT, taxonomy, snapshot_path=tmp_path / "absent.json")


def test_the_projection_is_unavailable_on_a_broken_snapshot(tmp_path, taxonomy):
    path = tmp_path / "snapshot.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LV.LiveUnavailable):
        LV.project(ROOT, taxonomy, snapshot_path=path)


def test_the_snapshot_age_is_measured(tmp_path, taxonomy):
    path = _snapshot(tmp_path, [], minutes_ago=5.0)
    age = LV.project(ROOT, taxonomy, snapshot_path=path)["snapshot_age_minutes"]
    assert age is not None and 4.0 <= age <= 7.0


def test_a_missing_age_is_reported_as_unknown_not_zero(tmp_path, taxonomy):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"issues": []}), encoding="utf-8")
    projection = LV.project(ROOT, taxonomy, snapshot_path=path)
    assert projection["snapshot_age_minutes"] is None


# ---------------------------------------------------------------------------
# the mandate — the constitution must keep declaring the rule (issue #1183)
# ---------------------------------------------------------------------------
import mandate as MD  # noqa: E402
import provoke as PV  # noqa: E402


def _mandate_tree(tmp_path: Path, mutate=None) -> Path:
    root = tmp_path / "tree"
    for doc in MD.DOCS:
        target = root / doc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / doc).read_text(encoding="utf-8"), encoding="utf-8")
    if mutate is not None:
        mutate(root)
    return root


def test_the_repository_declares_the_mandate():
    assert MD.check(ROOT) == ()


def test_the_mandate_names_five_contract_documents():
    assert len(MD.DOCS) == 5
    assert "AGENTS.md" in MD.DOCS
    assert "docs/GOLDEN-RULES.md" in MD.DOCS


def test_every_contract_doc_declares_the_shared_markers():
    for doc, markers in MD.contract():
        for marker in MD.SHARED_MARKERS:
            assert marker in markers, "%s is missing the shared marker %r" % (doc, marker)


def test_agents_md_must_name_the_authority_file_and_the_gate():
    markers = dict(MD.contract())["AGENTS.md"]
    assert "governance/tagging/taxonomy.yaml" in markers
    assert "scripts/check-tagging.sh" in markers


def test_the_spine_declares_its_numbered_entry():
    assert "AO-GR-28" in dict(MD.contract())["docs/GOLDEN-RULES.md"]


def test_a_stripped_marker_is_refused_by_name(tmp_path):
    def strip(root: Path) -> None:
        doc = root / "docs" / "QA-GATE.md"
        text = doc.read_text(encoding="utf-8")
        mutated = text.replace("lifecycle", "SDLC-stage")
        assert mutated != text, "the marker was not present to strip"
        doc.write_text(mutated, encoding="utf-8")

    findings = MD.check(_mandate_tree(tmp_path, strip))
    errs = M.errors(findings)
    assert MD.CODE_MISSING_MARKER in {f.code for f in errs}
    assert any(f.subject == "docs/QA-GATE.md" for f in errs)
    assert any("lifecycle" in f.message for f in errs)


def test_a_missing_contract_doc_is_refused_by_name(tmp_path):
    def drop(root: Path) -> None:
        (root / "docs" / "GOVERNANCE.md").unlink()

    findings = MD.check(_mandate_tree(tmp_path, drop))
    assert MD.CODE_MISSING_DOC in {f.code for f in findings}
    assert any(f.subject == "docs/GOVERNANCE.md" for f in findings)


def test_the_unmutated_tree_is_refused_nothing(tmp_path):
    assert MD.check(_mandate_tree(tmp_path)) == ()


def test_every_mandate_code_is_a_declared_refusal(taxonomy):
    assert MD.CODE_MISSING_DOC in taxonomy.refusal_ids
    assert MD.CODE_MISSING_MARKER in taxonomy.refusal_ids


def test_the_mandate_is_provoked_in_both_directions():
    assert MD.CODE_MISSING_DOC in PV.PROVOCATIONS_BY_REFUSAL
    assert MD.CODE_MISSING_MARKER in PV.PROVOCATIONS_BY_REFUSAL


# ---------------------------------------------------------------------------
# the filing seam's tag defaults must be legal (issue #1182)
# ---------------------------------------------------------------------------
CONFORMANCE_POLICY = ROOT / "governance" / "conformance" / "policy.yaml"


def test_the_filing_defaults_are_legal_values(taxonomy):
    assert M.filing_drift(taxonomy, CONFORMANCE_POLICY) == []


def test_the_filing_default_drift_code_is_a_declared_refusal(taxonomy):
    assert M.CODE_FILING_DEFAULT_DRIFT in taxonomy.refusal_ids
    assert M.CODE_FILING_DEFAULT_DRIFT in M.REFUSAL_CODES


def test_an_illegal_filing_default_is_refused_by_name(tmp_path, taxonomy):
    text = CONFORMANCE_POLICY.read_text(encoding="utf-8")
    mutant_text = text.replace("    posture: overall", "    posture: mythic", 1)
    assert mutant_text != text, "the posture default was not present to mutate"
    mutant = tmp_path / "policy.yaml"
    mutant.write_text(mutant_text, encoding="utf-8")
    findings = M.filing_drift(taxonomy, mutant)
    assert M.CODE_FILING_DEFAULT_DRIFT in {f.code for f in findings}
    assert any("mythic" in f.message for f in findings)


def test_an_undeclared_filing_tag_is_refused_by_name(tmp_path, taxonomy):
    text = CONFORMANCE_POLICY.read_text(encoding="utf-8")
    mutant_text = text.replace("tags: [posture, lifecycle]", "tags: [posture, lifecycle, mood]", 1)
    assert mutant_text != text
    mutant = tmp_path / "policy.yaml"
    mutant.write_text(mutant_text, encoding="utf-8")
    findings = M.filing_drift(taxonomy, mutant)
    assert M.CODE_FILING_DEFAULT_DRIFT in {f.code for f in findings}
    assert any("mood" in f.message for f in findings)


def test_a_missing_filing_default_is_refused(tmp_path, taxonomy):
    text = CONFORMANCE_POLICY.read_text(encoding="utf-8")
    mutant_text = text.replace("    posture: overall", "    posture: ")
    assert mutant_text != text
    mutant = tmp_path / "policy.yaml"
    mutant.write_text(mutant_text, encoding="utf-8")
    findings = M.filing_drift(taxonomy, mutant)
    assert M.CODE_FILING_DEFAULT_DRIFT in {f.code for f in findings}


def test_an_unreadable_policy_is_refused(tmp_path, taxonomy):
    findings = M.filing_drift(taxonomy, tmp_path / "absent.yaml")
    assert M.CODE_FILING_DEFAULT_DRIFT in {f.code for f in findings}


# -- the futureproof mechanism chain (#1193) ---------------------------------
# The e2e capstone proves every classification mechanism's chain end to end:
# authority declared -> gate wired -> gate falsifiable. These tests drive that
# table's own invariants with a clean twin AND a mutant, so the capstone's links
# cannot pass while doing nothing.

def _discovered() -> set:
    return E._discovered_gates(ROOT)


def _denylisted() -> set:
    return E._denylisted_gates(ROOT)


def test_mechanisms_are_complete():
    detail = E.mechanisms_complete(E.MECHANISMS, E.EXPECTED_MECHANISMS)
    assert "10 mechanism(s)" in detail


def test_mechanisms_are_disjoint():
    detail = E.mechanisms_disjoint(E.MECHANISMS)
    assert "12 gate(s)" in detail


def test_every_mechanism_chain_holds_here():
    discovered, denylisted = _discovered(), _denylisted()
    for mech in E.MECHANISMS:
        detail = E.mechanism_ok(ROOT, mech, discovered, denylisted)
        assert mech["id"] in detail or "gate(s)" in detail


def test_mechanism_chain_refuses_a_dead_gate():
    # A mechanism whose gate script no longer exists must be refused by name.
    mech = {**E.MECHANISMS[0], "gates": ["surface-class", "does-not-exist"]}
    with pytest.raises(AssertionError) as exc:
        E.mechanism_ok(ROOT, mech, _discovered(), _denylisted())
    assert "does-not-exist" in str(exc.value)


def test_mechanism_chain_refuses_a_denylisted_gate():
    # A mechanism whose gate is denylisted is silently unwired: refuse it.
    mech = {**E.MECHANISMS[1], "gates": ["shell-patterns", "pr-contract"]}
    with pytest.raises(AssertionError) as exc:
        E.mechanism_ok(ROOT, mech, _discovered(), _denylisted() | {"pr-contract"})
    assert "denylisted" in str(exc.value)


def test_mechanism_chain_refuses_a_missing_provocation():
    mech = {**E.MECHANISMS[3], "provocation": ("governance/lessons/nope.py", "")}
    with pytest.raises(AssertionError) as exc:
        E.mechanism_ok(ROOT, mech, _discovered(), _denylisted())
    assert "provocation artifact" in str(exc.value)


def test_mechanisms_complete_refuses_drift():
    dropped = [m for m in E.MECHANISMS if m["id"] != "rca"]
    with pytest.raises(AssertionError) as exc:
        E.mechanisms_complete(dropped, E.EXPECTED_MECHANISMS)
    assert "rca" in str(exc.value)


def test_mechanisms_disjoint_refuses_a_shared_gate():
    # Two mechanisms claiming the same gate is a hidden authority conflict.
    shared = E.MECHANISMS[:2]
    shared[0] = {**shared[0], "gates": ["shell-patterns"]}
    with pytest.raises(AssertionError) as exc:
        E.mechanisms_disjoint(shared)
    assert "two mechanisms" in str(exc.value)
