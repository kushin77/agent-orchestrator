"""The declaration set, its graph invariants, and its lookups (issue #650)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.erp.crm import definitions as declaration
from integrations.erp.crm.definitions import (
    LOCAL_DECLARATION,
    SOURCE_LOCAL,
    KindDefinition,
    load,
)
from integrations.erp.crm.model import KINDS, Refused


def test_the_shipped_declaration_loads(definitions) -> None:
    assert definitions.source == SOURCE_LOCAL
    assert definitions.schema_version == 1
    assert sorted(definitions.kinds) == sorted(KINDS)
    assert len(definitions.vocabularies) == 9
    assert sorted(definitions.sla_policies) == ["p1", "p2", "p3"]


def test_the_shipped_declaration_is_json() -> None:
    assert isinstance(json.loads(LOCAL_DECLARATION.read_text(encoding="utf-8")), dict)


def test_every_kind_declares_a_total_machine(definitions) -> None:
    for name in sorted(definitions.kinds):
        kind = definitions.kind(name)
        assert kind.initial in kind.states
        assert set(kind.transitions) == set(kind.states)
        for state, targets in kind.transitions.items():
            for target in targets:
                assert target in kind.states, f"{name}.{state} -> {target}"
        assert set(kind.required_fields) <= set(kind.allowed_fields)
        assert set(kind.open_states) <= set(kind.states)


def test_terminal_states_are_the_ones_with_no_transition(definitions) -> None:
    assert definitions.kind("inspection").terminal_states == ("passed",)
    assert definitions.kind("timesheet").terminal_states == ("approved",)
    assert definitions.kind("support-issue").terminal_states == ("closed",)
    assert definitions.kind("lead").terminal_states == ("converted", "lost")


def test_open_states_drive_child_work(definitions) -> None:
    task = definitions.kind("task")
    assert task.is_open("open") and task.is_open("in-progress")
    assert not task.is_open("done") and not task.is_open("cancelled")


def test_a_kind_that_declares_no_open_states_accepts_no_child_work() -> None:
    """Fail-closed: a family that forgot its open states does not accept closed work."""
    undeclared = KindDefinition(
        name="widget",
        states=("new", "spent"),
        initial="new",
        transitions={"new": ("spent",), "spent": ()},
        allowed_fields=(),
        required_fields=(),
    )
    assert undeclared.open_states == ()
    assert not undeclared.is_open("new")
    assert not undeclared.is_open("spent")


def test_undeclared_kind_vocabulary_and_policy_are_refused_by_name(definitions) -> None:
    with pytest.raises(Refused, match="unknown-kind") as caught:
        definitions.kind("sprocket")
    assert "sprocket" in caught.value.detail

    with pytest.raises(Refused, match="unknown-vocabulary") as caught:
        definitions.vocabulary("sprocket-stages")
    assert "sprocket-stages" in caught.value.detail

    with pytest.raises(Refused, match="unknown-policy") as caught:
        definitions.policy("p9")
    assert "p9" in caught.value.detail


def test_the_declaration_round_trips_through_load(definitions) -> None:
    assert load(definitions.to_dict()).to_dict() == definitions.to_dict()


def test_a_schema_violation_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    del broken["vocabularies"]
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "vocabularies" in caught.value.detail


def test_a_transition_to_an_undeclared_state_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    broken["kinds"]["lead"]["transitions"]["qualified"] = ["sprocketed"]
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "sprocketed" in caught.value.detail


def test_a_machine_with_no_entry_for_a_declared_state_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    del broken["kinds"]["lead"]["transitions"]["lost"]
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "no entry for declared state" in caught.value.detail


def test_an_initial_state_outside_the_kinds_own_states_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    broken["kinds"]["lead"]["initial"] = "delivered"
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "not one of its own states" in caught.value.detail


def test_a_required_field_that_is_not_allowed_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    broken["kinds"]["lead"]["requiredFields"] = ["company", "planet"]
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "planet" in caught.value.detail


def test_a_reference_to_an_undeclared_vocabulary_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    broken["kinds"]["lead"]["vocabularies"]["stage"] = "sprocket-stages"
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "undeclared vocabulary 'sprocket-stages'" in caught.value.detail


def test_an_unknown_declared_field_type_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    broken["kinds"]["lead"]["fieldTypes"]["value"] = "sausage"
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "sausage" in caught.value.detail


def test_a_declaration_that_omits_a_kind_this_module_owns_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    del broken["kinds"]["inspection"]
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "inspection" in caught.value.detail


def test_a_resolution_window_shorter_than_its_response_window_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    broken["slaPolicies"]["p1"]["resolveMinutes"] = 1
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "shorter than" in caught.value.detail


def test_a_warning_percentage_outside_the_open_interval_is_refused(definitions) -> None:
    broken = definitions.to_dict()
    broken["slaPolicies"]["p1"]["warnPercent"] = 100
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(broken)
    assert "warnPercent" in caught.value.detail


def test_a_missing_declaration_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(tmp_path / "absent.json")
    assert "not found" in caught.value.detail


def test_an_unreadable_declaration_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "definitions.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(Refused, match="definitions-invalid"):
        load(path)


def test_an_unsupported_keyword_in_the_schema_is_refused_by_name(definitions, tmp_path: Path) -> None:
    """A schema that promises more than the validator enforces is a refusal, not a pass."""
    schema_path = tmp_path / "definitions.schema.json"
    schema_path.write_text(
        json.dumps({"type": "object", "required": ["kinds"], "properties": {"kinds": {"type": "object", "minProperties": 1}}}),
        encoding="utf-8",
    )
    with pytest.raises(Refused, match="definitions-invalid") as caught:
        load(definitions.to_dict(), schema_path=schema_path)
    assert "minProperties" in caught.value.detail


def test_sla_policy_windows_are_ordered(definitions) -> None:
    for name in sorted(definitions.sla_policies):
        policy = definitions.sla_policies[name]
        assert 0 < policy.warn_percent < 100
        assert policy.resolve_minutes >= policy.respond_minutes >= 1
        assert policy.to_dict() == {
            "respondMinutes": policy.respond_minutes,
            "resolveMinutes": policy.resolve_minutes,
            "warnPercent": policy.warn_percent,
        }


def test_kind_definition_helpers(definitions) -> None:
    lead = definitions.kind("lead")
    assert lead.is_state("new") and not lead.is_state("delivered")
    assert lead.allows("new", "contacted") and not lead.allows("new", "qualified")
    assert lead.field_type("value") == "integer"
    assert lead.vocabulary_for("stage") == "lead-stages"
    assert lead.vocabulary_for("company") is None
