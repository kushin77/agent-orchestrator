"""The asset contract: coverage, parity and provenance, each provoked.

``check_assets`` returns the ways the shipped files contradict their own
contract, and the acceptance criterion is that it returns nothing. A check that
returns nothing because it cannot return anything is worthless, so every rule
below is **provoked** in a scratch copy of the tree and the resulting problem
must name the file and the field that was broken. One test also proves a clean
copy is refused nothing, so the provocations cannot be passing because the check
is always red.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest

from integrations.erp.core import validators

from .conftest import CORE


def scratch(tmp_path: Path) -> Path:
    """A writable copy of the model tree, without any bytecode cache."""
    target = tmp_path / "core"
    shutil.copytree(CORE, target, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    return target


def problems_for(root: Path) -> List[str]:
    return validators.load_model(root).check_assets()


def _edit_json(path: Path, edit: Callable[[Dict[str, Any]], None]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    edit(document)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# --- the shipped assets are coherent ---------------------------------------


def test_the_shipped_model_has_no_asset_problems(model) -> None:
    assert model.check_assets() == []


def test_every_schema_carries_id_title_and_provenance(model) -> None:
    """Acceptance criterion 1, asserted directly rather than by implication."""
    assert model.schema_files, "no schemas were loaded"
    for filename, schema in model.schema_files.items():
        assert isinstance(schema.get("$id"), str) and schema["$id"], filename
        assert isinstance(schema.get("title"), str) and schema["title"], filename
        harvest = schema[validators.PROVENANCE_KEY]
        for field in validators.PROVENANCE_REQUIRED:
            assert field in harvest, f"{filename}: missing {field}"
        assert harvest["source"]["repo"] == "frappe/erpnext", filename
        assert harvest["source"]["mode"] == validators.PROVENANCE_REQUIRED_MODE, filename
        assert harvest["source"]["license"].startswith("GPL"), filename


def test_every_schema_validates_its_own_id_path(model) -> None:
    for filename, schema in model.schema_files.items():
        assert schema["$id"].endswith(f"schemas/{filename}")


def test_provenance_json_accounts_for_every_schema_both_ways(model) -> None:
    records = set(model.provenance["schemas"])
    assert records == set(model.schema_files)


def test_lifecycle_coverage_is_exactly_the_workflow_set(model) -> None:
    assert set(model.lifecycle_kinds()) == set(model.workflows.documents())
    assert len(model.workflows) == 8


def test_no_upstream_artifact_is_vendored_into_the_tree() -> None:
    """GPL-3.0 upstream: the tree carries our files only, never a copy.

    Two assertions: every shipped file is a kind this model authors, and no
    shipped data or code file carries upstream licence text — which a copy
    would, and a pattern reference does not. The suite itself is excluded: it
    names the markers it searches for, which is not a copy of anything.
    """
    licence_markers = ("Copyright (c)", "GNU General Public License", "GNU GPL")
    for path in sorted(CORE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or "tests" in path.parts:
            continue
        assert path.suffix in {".py", ".json", ".yaml", ".md"}, path
        if path.suffix not in {".py", ".json", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in licence_markers:
            assert marker not in text, f"{path} carries upstream licence text"


# --- provocations ----------------------------------------------------------


def test_a_clean_scratch_copy_is_refused_nothing(tmp_path: Path) -> None:
    assert problems_for(scratch(tmp_path)) == []


def test_a_schema_without_its_harvest_record_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    target = root / "schemas" / "sales-order.schema.json"
    _edit_json(target, lambda doc: doc.pop(validators.PROVENANCE_KEY))
    problems = problems_for(root)
    assert any(
        "schemas/sales-order.schema.json" in problem and "harvest record" in problem
        for problem in problems
    ), problems


def test_a_schema_without_an_id_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    _edit_json(
        root / "schemas" / "party.schema.json", lambda doc: doc.pop("$id")
    )
    problems = problems_for(root)
    assert any("schemas/party.schema.json" in p and "$id" in p for p in problems), problems


def test_a_schema_without_a_title_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    _edit_json(
        root / "schemas" / "item.schema.json", lambda doc: doc.pop("title")
    )
    problems = problems_for(root)
    assert any("schemas/item.schema.json" in p and "title" in p for p in problems), problems


def test_an_id_that_names_another_path_is_refused(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    _edit_json(
        root / "schemas" / "quotation.schema.json",
        lambda doc: doc.__setitem__("$id", "https://example.invalid/other.json"),
    )
    problems = problems_for(root)
    assert any("does not name this path" in p for p in problems), problems


def test_a_vendored_harvest_mode_is_refused(tmp_path: Path) -> None:
    """The GPL boundary is enforced, not merely documented."""
    root = scratch(tmp_path)
    _edit_json(
        root / "schemas" / "party.schema.json",
        lambda doc: doc[validators.PROVENANCE_KEY]["source"].__setitem__(
            "mode", "copied"
        ),
    )
    problems = problems_for(root)
    assert any("copied or vendored" in p for p in problems), problems


def test_an_empty_harvest_list_is_refused(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    _edit_json(
        root / "schemas" / "party.schema.json",
        lambda doc: doc[validators.PROVENANCE_KEY].__setitem__("harvest", []),
    )
    problems = problems_for(root)
    assert any("harvest is empty" in p for p in problems), problems


def test_a_missing_provenance_record_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    target = root / validators.PROVENANCE_FILENAME
    _edit_json(target, lambda doc: doc["schemas"].pop("party.schema.json"))
    problems = problems_for(root)
    assert any("no record for schemas/party.schema.json" in p for p in problems), problems


def test_a_licence_that_is_not_the_upstream_one_is_refused(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    _edit_json(
        root / validators.PROVENANCE_FILENAME,
        lambda doc: doc["upstream"].__setitem__("license", "MIT"),
    )
    problems = problems_for(root)
    assert any("GPL" in p for p in problems), problems


def test_a_lifecycle_without_a_workflow_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    (root / "workflows" / "sales-order.yaml").unlink()
    problems = problems_for(root)
    assert any(
        "schemas/sales-order.schema.json" in p and "no workflow" in p for p in problems
    ), problems


def test_a_workflow_without_a_lifecycle_schema_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    _edit_json(
        root / "schemas" / "party.schema.json",
        lambda doc: doc.__setitem__(validators.LIFECYCLE_KEY, True),
    )
    problems = problems_for(root)
    assert any("no workflow declares" in p for p in problems), problems


def test_a_stray_workflow_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    (root / "workflows" / "milestone.yaml").write_text(
        "workflow: milestone-lifecycle\n"
        "document: milestone\n"
        "version: 1\n"
        "initial: draft\n"
        "states:\n"
        "  - name: draft\n"
        "    docstatus: 0\n"
        "  - name: cancelled\n"
        "    docstatus: 2\n"
        "    terminal: true\n"
        "transitions:\n"
        "  - action: cancel\n"
        "    from: draft\n"
        "    to: cancelled\n",
        encoding="utf-8",
    )
    problems = problems_for(root)
    assert any("milestone" in p and "workflow" in p for p in problems), problems


def test_a_state_that_drifts_from_the_schema_is_named(tmp_path: Path) -> None:
    """The parity invariant: the schema enum and the workflow cannot disagree."""
    root = scratch(tmp_path)
    target = root / "workflows" / "sales-order.yaml"
    target.write_text(
        target.read_text(encoding="utf-8").replace("completed", "finished"),
        encoding="utf-8",
    )
    problems = problems_for(root)
    assert any(
        "state enum" in p and "does not equal" in p for p in problems
    ), problems


def test_a_docstatus_that_drifts_from_the_schema_is_named(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    target = root / "workflows" / "quotation.yaml"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "    docstatus: 2\n    label: Cancelled",
            "    docstatus: 1\n    label: Cancelled",
            1,
        ),
        encoding="utf-8",
    )
    problems = problems_for(root)
    assert any("docstatus enum" in p for p in problems), problems


def test_a_second_schema_for_one_kind_is_refused(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    source = root / "schemas" / "party.schema.json"
    clone = json.loads(source.read_text(encoding="utf-8"))
    clone["$id"] = clone["$id"].replace("party.schema.json", "party-copy.schema.json")
    (root / "schemas" / "party-copy.schema.json").write_text(
        json.dumps(clone, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(validators.ErpError) as refusal:
        validators.load_model(root)
    assert refusal.value.code == "workflow_invalid"


def test_a_model_without_provenance_refuses_to_load(tmp_path: Path) -> None:
    root = scratch(tmp_path)
    (root / validators.PROVENANCE_FILENAME).unlink()
    with pytest.raises(validators.ErpError) as refusal:
        validators.load_model(root)
    assert refusal.value.code == "missing_provenance"
