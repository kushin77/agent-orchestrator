"""Startup validation gate tests (issue #26 acceptance #1).

An invalid policy must fail at deploy/startup — never at runtime — so these
tests assert that malformed and unsafe policies raise or produce errors from
the loader/gate, while valid ones load.
"""

from __future__ import annotations

import pytest

from policy import (
    DuplicatePolicyError,
    PolicyBundle,
    PolicyLoadError,
    PolicyValidationError,
    build_bundle,
    validate_paths,
)
from policy.errors import ControlError
from policy.loader import discover_policy_files, load_policy_file, policy_from_mapping


def _valid_doc(**overrides) -> dict:
    doc = {
        "id": "doc-policy",
        "rules": [
            {
                "id": "block-evil",
                "actions": ["evil.action"],
                "decision": "block",
                "reason": "evil action blocked",
            }
        ],
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# loader / policy_from_mapping
# ---------------------------------------------------------------------------


def test_valid_document_loads_with_defaults(make_policy):
    policy = make_policy(_valid_doc())
    assert policy.id == "doc-policy"
    assert policy.version == 1
    assert policy.default.value == "log"
    assert policy.enabled is True
    assert policy.controls == ()
    assert len(policy.rules) == 1
    assert policy.rules[0].decision.value == "block"


def test_missing_id_is_rejected():
    with pytest.raises(PolicyValidationError):
        policy_from_mapping(_valid_doc(id=None))


def test_block_without_reason_is_rejected(make_policy):
    doc = _valid_doc()
    del doc["rules"][0]["reason"]
    with pytest.raises(PolicyValidationError) as exc:
        make_policy(doc)
    assert "reason" in str(exc.value)


def test_duplicate_rule_ids_within_policy_are_rejected(make_policy):
    doc = _valid_doc()
    doc["rules"] = [dict(doc["rules"][0]), dict(doc["rules"][0])]
    with pytest.raises(PolicyValidationError) as exc:
        make_policy(doc)
    assert "duplicate rule id" in str(exc.value)


def test_unknown_condition_operator_is_rejected_at_startup(make_policy):
    doc = _valid_doc()
    doc["rules"][0]["condition"] = {"path": "x", "op": "explode", "value": 1}
    with pytest.raises(PolicyValidationError) as exc:
        make_policy(doc)
    assert "unknown operator" in str(exc.value)


def test_invalid_regex_in_condition_is_rejected_at_startup(make_policy):
    doc = _valid_doc()
    doc["rules"][0]["condition"] = {"path": "x", "op": "regex", "value": "(["}
    with pytest.raises(PolicyValidationError) as exc:
        make_policy(doc)
    assert "regular expression" in str(exc.value)


def test_non_mapping_yaml_is_a_load_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(PolicyLoadError):
        load_policy_file(bad)


def test_unparseable_yaml_is_a_load_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyLoadError):
        load_policy_file(bad)


def test_missing_path_is_a_load_error():
    with pytest.raises(PolicyLoadError):
        discover_policy_files(["definitely/not/here"])


def test_container_file_loads_multiple_policies(tmp_path, make_policy):
    path = tmp_path / "container.yaml"
    path.write_text(
        "policies:\n"
        "  - id: first-policy\n"
        "    rules:\n"
        "      - id: r1\n"
        "        actions: [a.b]\n"
        "        decision: log\n"
        "        reason: observe\n"
        "  - id: second-policy\n"
        "    rules:\n"
        "      - id: r2\n"
        "        actions: [c.d]\n"
        "        decision: block\n"
        "        reason: block it\n",
        encoding="utf-8",
    )
    policies = load_policy_file(path)
    assert [policy.id for policy in policies] == ["first-policy", "second-policy"]


# ---------------------------------------------------------------------------
# bundle validation across files
# ---------------------------------------------------------------------------


def test_duplicate_policy_ids_across_bundle_are_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "id: shared-id\nrules:\n  - id: r1\n    actions: [a.b]\n    decision: log\n",
        encoding="utf-8",
    )
    (tmp_path / "b.yaml").write_text(
        "id: shared-id\nrules:\n  - id: r2\n    actions: [c.d]\n    decision: log\n",
        encoding="utf-8",
    )
    report = validate_paths([str(tmp_path)])
    assert not report.ok
    assert any("duplicate policy id" in error for error in report.errors)
    with pytest.raises(PolicyValidationError):
        build_bundle([str(tmp_path)])


def test_gated_policy_without_registry_is_rejected(tmp_path):
    (tmp_path / "p.yaml").write_text(
        "id: gated\ncontrols: [some-control]\n"
        "rules:\n  - id: r1\n    actions: [a.b]\n    decision: log\n",
        encoding="utf-8",
    )
    report = validate_paths([str(tmp_path)])
    assert not report.ok
    assert any("no controls registry" in error for error in report.errors)


def test_gated_policy_referencing_unregistered_control_is_rejected(tmp_path, shipped_controls):
    (tmp_path / "p.yaml").write_text(
        "id: gated\ncontrols: [no-such-control]\n"
        "rules:\n  - id: r1\n    actions: [a.b]\n    decision: log\n",
        encoding="utf-8",
    )
    report = validate_paths([str(tmp_path)], controls=shipped_controls)
    assert not report.ok
    assert any("unregistered control" in error for error in report.errors)


def test_build_bundle_returns_bundle_on_valid_input(tmp_path):
    (tmp_path / "p.yaml").write_text(
        "id: fine-policy\nrules:\n  - id: r1\n    actions: [a.b]\n    decision: log\n",
        encoding="utf-8",
    )
    bundle = build_bundle([str(tmp_path)])
    assert isinstance(bundle, PolicyBundle)
    assert bundle.get("fine-policy") is not None


def test_validate_report_counts_files_and_policies(tmp_path):
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            f"id: {name[:1]}-policy\n"
            "rules:\n  - id: r1\n    actions: [a.b]\n    decision: log\n",
            encoding="utf-8",
        )
    report = validate_paths([str(tmp_path)])
    assert report.ok
    assert report.file_count == 2
    assert report.policy_count == 2
    assert "valid" in report.summary()


def test_bundle_merge_overlay_semantics(make_policy):
    base_policy = make_policy(_valid_doc(id="same"))
    overlay_policy = make_policy(_valid_doc(id="same", description="overlay"))
    base = PolicyBundle((base_policy,))
    overlay = PolicyBundle((overlay_policy,))
    merged = base.merge(overlay)
    assert merged.get("same").description == "overlay"
    assert len(merged) == 1


def test_bundle_assembler_rejects_duplicates(make_policy):
    with pytest.raises(DuplicatePolicyError):
        PolicyBundle((make_policy(_valid_doc(id="dup")), make_policy(_valid_doc(id="dup"))))
