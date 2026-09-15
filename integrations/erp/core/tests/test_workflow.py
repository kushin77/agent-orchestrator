"""The workflow data model: loadable as data, and refusing illegal moves.

Acceptance criterion 2 is that the workflow data is loadable **without importing
or editing ``engine/``**. That is proven two ways: a static scan of the package's
import statements, and a subprocess whose import machinery raises if ``engine``
is imported at all — so a future refactor that reaches for the engine fails here
rather than in a deployment.

Acceptance criterion 3's second half is that a document which jumps states is
refused. Every structural invariant the loader declares is provoked in the
parameterised table below, so a rule that stopped biting cannot pass.
"""

from __future__ import annotations

import ast
import copy
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

import pytest

from integrations.erp.core import workflow as wf

from . import documents
from .conftest import CORE, ROOT

ENGINE_FREE_MODULES = ("workflow.py", "schema.py", "validators.py", "errors.py")


def _workflow_data(**over: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "workflow": "sample-lifecycle",
        "document": "sample",
        "version": 1,
        "initial": "draft",
        "states": [
            {"name": "draft", "docstatus": 0},
            {"name": "submitted", "docstatus": 1},
            {"name": "cancelled", "docstatus": 2, "terminal": True},
        ],
        "transitions": [
            {"action": "submit", "from": "draft", "to": "submitted"},
            {"action": "cancel", "from": "submitted", "to": "cancelled"},
        ],
    }
    data.update(over)
    return data


# --- the shipped data loads -------------------------------------------------


def test_every_workflow_loads_and_validates_against_the_meta_schema(
    model, validator
) -> None:
    meta = model.schema_files["workflow.schema.json"]
    assert len(model.workflows) == 8
    for item in model.workflows:
        problems = validator.violations(item.to_data(), meta, where=item.workflow)
        assert problems == [], problems


def test_every_workflow_starts_in_the_draft_state(model) -> None:
    for item in model.workflows:
        assert item.initial == "draft", item.workflow
        assert item.state(item.initial).docstatus == wf.DOCSTATUS_DRAFT


def test_every_terminal_state_is_the_end_of_the_line(model) -> None:
    for item in model.workflows:
        for terminal in item.terminal_states():
            assert item.legal_targets(terminal) == (), (item.workflow, terminal)


def test_every_state_is_reachable_from_the_initial_state(model) -> None:
    for item in model.workflows:
        reachable = set(item.reachable_from(item.initial))
        assert reachable == set(item.state_names()), item.workflow


def test_the_docstatus_vocabulary_is_closed_and_draft_is_zero(model) -> None:
    assert wf.DOCSTATUS_VOCABULARY == (0, 1, 2)
    for item in model.workflows:
        for state in item.states:
            assert state.docstatus in wf.DOCSTATUS_VOCABULARY


def test_docstatus_never_moves_backwards(model) -> None:
    for item in model.workflows:
        for transition in item.transitions:
            source = item.state(transition.from_state).docstatus
            destination = item.state(transition.to).docstatus
            assert destination >= source, (item.workflow, transition.action)


def test_a_workflow_round_trips_through_its_data_shape(model) -> None:
    for item in model.workflows:
        again = wf.Workflow.from_data(item.to_data(), source=item.source)
        assert again.state_names() == item.state_names()
        assert again.transitions == item.transitions
        assert again.to_data() == item.to_data()


def test_the_legal_path_from_draft_is_submitted_then_completed(model) -> None:
    order = model.workflow_for("sales-order")
    assert order.path("draft", "completed") == ["draft", "submitted", "completed"]
    assert order.path("draft", "cancelled") == ["draft", "cancelled"]
    assert order.path("submitted", "draft") == []


# --- the moves a document may make -----------------------------------------


def test_a_legal_advance_returns_the_next_state(model) -> None:
    order = model.workflow_for("sales-order")
    assert order.next_state("draft", "submit") == "submitted"
    assert order.next_state("submitted", "complete") == "completed"
    assert order.next_state("submitted", "cancel") == "cancelled"


def test_an_action_the_state_does_not_declare_is_refused(model) -> None:
    order = model.workflow_for("sales-order")
    with pytest.raises(wf.ErpError) as refusal:
        order.next_state("draft", "complete")
    assert refusal.value.code == "unknown_action"
    assert refusal.value.details["available"] == ["cancel", "submit"]


def test_an_undeclared_state_is_refused(model) -> None:
    order = model.workflow_for("sales-order")
    with pytest.raises(wf.ErpError) as refusal:
        order.next_state("approved", "submit")
    assert refusal.value.code == "unknown_state"


def test_a_state_jump_is_refused_by_name(model) -> None:
    """Acceptance criterion 3: draft may not jump straight to completed."""
    order = model.workflow_for("sales-order")
    with pytest.raises(wf.ErpError) as refusal:
        order.assert_move("draft", "completed")
    assert refusal.value.code == "state_jumped"
    assert refusal.value.details["legal"] == ["cancelled", "submitted"]


def test_asserting_a_target_the_action_does_not_reach_is_refused(model) -> None:
    order = model.workflow_for("sales-order")
    with pytest.raises(wf.ErpError) as refusal:
        order.next_state("draft", "submit", target="completed")
    assert refusal.value.code == "state_jumped"
    assert refusal.value.details["reached"] == "submitted"
    assert refusal.value.details["asserted"] == "completed"


def test_a_legal_move_is_accepted_by_assert_move(model) -> None:
    for item in model.workflows:
        for transition in item.transitions:
            assert item.assert_move(transition.from_state, transition.to) == (
                transition.to
            )


def test_advancing_a_document_refuses_a_kind_without_a_workflow(model) -> None:
    document = documents.party()
    with pytest.raises(wf.ErpError) as refusal:
        model.advance(document, "submit")
    assert refusal.value.code == "unknown_workflow"


def test_advancing_a_document_requires_a_state(model) -> None:
    document = documents.sales_order()
    document.pop("state")
    with pytest.raises(wf.ErpError) as refusal:
        model.advance(document, "submit")
    assert refusal.value.code == "invalid_body"


# --- the structural refusals ------------------------------------------------


def _duplicate_state(data: Dict[str, Any]) -> None:
    data["states"][1]["name"] = "draft"


def _unknown_initial(data: Dict[str, Any]) -> None:
    data["initial"] = "approved"


def _initial_is_not_draft(data: Dict[str, Any]) -> None:
    data["initial"] = "submitted"


def _transition_names_an_undeclared_state(data: Dict[str, Any]) -> None:
    data["transitions"][0]["to"] = "approved"


def _two_transitions_for_one_action(data: Dict[str, Any]) -> None:
    data["transitions"].append({"action": "submit", "from": "draft", "to": "cancelled"})


def _outgoing_transition_from_a_terminal_state(data: Dict[str, Any]) -> None:
    data["transitions"].append(
        {"action": "reopen", "from": "cancelled", "to": "draft"}
    )


def _unreachable_state(data: Dict[str, Any]) -> None:
    data["states"].append({"name": "parked", "docstatus": 1})


def _docstatus_moves_backwards(data: Dict[str, Any]) -> None:
    data["states"].append({"name": "reopened", "docstatus": 0})
    data["transitions"].append(
        {"action": "reopen", "from": "submitted", "to": "reopened"}
    )


def _no_states(data: Dict[str, Any]) -> None:
    data["states"] = []


def _missing_initial(data: Dict[str, Any]) -> None:
    data.pop("initial")


def _empty_action(data: Dict[str, Any]) -> None:
    data["transitions"][0]["action"] = ""


def _bad_docstatus(data: Dict[str, Any]) -> None:
    data["states"][0]["docstatus"] = 7


def _not_a_mapping(data: Dict[str, Any]) -> None:
    data.clear()
    data.update({"workflow": "sample"})


STRUCTURAL_REFUSALS: List[Dict[str, Any]] = [
    {"why": "a duplicate state name", "edit": _duplicate_state},
    {"why": "an initial state that is not declared", "edit": _unknown_initial},
    {"why": "an initial state that is not the draft", "edit": _initial_is_not_draft},
    {
        "why": "a transition naming an undeclared state",
        "edit": _transition_names_an_undeclared_state,
    },
    {
        "why": "two transitions for one (state, action), so the target is undecidable",
        "edit": _two_transitions_for_one_action,
    },
    {
        "why": "an outgoing transition from a terminal state",
        "edit": _outgoing_transition_from_a_terminal_state,
    },
    {"why": "a state nothing can reach", "edit": _unreachable_state},
    {"why": "a transition that moves docstatus backwards", "edit": _docstatus_moves_backwards},
    {"why": "no states at all", "edit": _no_states},
    {"why": "no initial state", "edit": _missing_initial},
    {"why": "a transition with an empty action", "edit": _empty_action},
    {"why": "a docstatus outside the closed vocabulary", "edit": _bad_docstatus},
    {"why": "data that is not a mapping", "edit": _not_a_mapping},
]


@pytest.mark.parametrize(
    "case", STRUCTURAL_REFUSALS, ids=[case["why"] for case in STRUCTURAL_REFUSALS]
)
def test_a_broken_workflow_is_refused(case: Dict[str, Any]) -> None:
    data = copy.deepcopy(_workflow_data())
    case["edit"](data)
    with pytest.raises(wf.ErpError) as refusal:
        wf.Workflow.from_data(data, source="sample.yaml")
    assert refusal.value.code == "workflow_invalid"
    assert refusal.value.message


def test_a_broken_workflow_is_refused_before_it_can_become_a_document_kind() -> None:
    """The refusal is the loader's, so no caller can hold an invalid workflow."""
    data = copy.deepcopy(_workflow_data())
    _outgoing_transition_from_a_terminal_state(data)
    with pytest.raises(wf.ErpError):
        wf.Workflow.from_data(data)


def test_an_unknown_workflow_lookup_is_refused(model) -> None:
    with pytest.raises(wf.ErpError) as refusal:
        model.workflows.by_document("milestone")
    assert refusal.value.code == "unknown_workflow"
    assert "sales-order" in refusal.value.details["known"]


def test_a_missing_workflow_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(wf.ErpError):
        wf.load_workflow_file(tmp_path / "absent.yaml")
    with pytest.raises(wf.ErpError):
        wf.load_workflows(tmp_path / "absent-directory")


def test_an_empty_workflow_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(wf.ErpError) as refusal:
        wf.load_workflows(tmp_path)
    assert refusal.value.code == "workflow_invalid"


def test_a_second_workflow_for_one_document_is_refused(tmp_path: Path) -> None:
    body = (
        "workflow: one\n"
        "document: sample\n"
        "initial: draft\n"
        "states:\n"
        "  - name: draft\n"
        "    docstatus: 0\n"
    )
    (tmp_path / "a.yaml").write_text(body, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(
        body.replace("workflow: one", "workflow: two"), encoding="utf-8"
    )
    with pytest.raises(wf.ErpError) as refusal:
        wf.load_workflows(tmp_path)
    assert "more than one workflow" in refusal.value.message


# --- the engine boundary ----------------------------------------------------


def test_no_module_in_the_package_imports_the_engine() -> None:
    """A static scan: no import of ``engine`` anywhere in the package."""
    offenders: List[str] = []
    for name in ENGINE_FREE_MODULES:
        tree = ast.parse((CORE / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{name}: {alias.name}"
                    for alias in node.names
                    if alias.name == "engine" or alias.name.startswith("engine.")
                ]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "engine" or node.module.startswith("engine."):
                    offenders.append(f"{name}: {node.module}")
    assert offenders == []


def test_the_workflow_data_loads_in_a_process_where_the_engine_cannot_be_imported() -> None:
    """Acceptance criterion 2, proven by making the engine import fatal."""
    script = textwrap.dedent(
        """
        import sys

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name == "engine" or name.startswith("engine."):
                    raise AssertionError("the engine was imported: " + name)
                return None

        sys.meta_path.insert(0, Blocker())
        sys.path.insert(0, {root!r})

        from integrations.erp.core import workflow

        loaded = workflow.load_workflows({workflows!r})
        assert len(loaded) == 8, len(loaded)
        leaked = sorted(
            name for name in sys.modules if name == "engine" or name.startswith("engine.")
        )
        assert leaked == [], leaked
        print("ENGINE-FREE OK")
        """
    ).format(root=str(ROOT), workflows=str(CORE / "workflows"))
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert "ENGINE-FREE OK" in result.stdout


def test_the_document_model_also_loads_without_the_engine() -> None:
    """The validators too, not only the raw workflow loader."""
    script = textwrap.dedent(
        """
        import sys

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name == "engine" or name.startswith("engine."):
                    raise AssertionError("the engine was imported: " + name)
                return None

        sys.meta_path.insert(0, Blocker())
        sys.path.insert(0, {root!r})

        from integrations.erp.core import validators

        model = validators.load_model()
        assert model.check_assets() == []
        print("ENGINE-FREE MODEL OK")
        """
    ).format(root=str(ROOT))
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert "ENGINE-FREE MODEL OK" in result.stdout
