"""The frozen row shape, and the generator held to it before the document leaves.

The schema is an artifact (``module-registry.schema.json``) and the validator is a
stdlib-only subset implementation, so the check runs offline and deterministically.
These tests prove three things the artifact is worth nothing without: the packaged
schema covers the vocabulary exactly, a malformed row is rejected **by path**, and
``registry.build`` refuses to emit a document that violates it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from governance.modules import registry, schema
from governance.modules.model import REFUSAL_CODES

REPO_ROOT = Path(__file__).resolve().parents[3]


def mutants() -> List[Dict[str, Any]]:
    """Named edits that each break the shape in one place."""

    def unknown_state(doc: Dict[str, Any]) -> None:
        doc["modules"][0]["state"] = "pending"

    def missing_field(doc: Dict[str, Any]) -> None:
        del doc["modules"][0]["health"]

    def undeclared_field(doc: Dict[str, Any]) -> None:
        doc["modules"][0]["extra"] = 1

    def second_state(doc: Dict[str, Any]) -> None:
        doc["states"] = doc["states"][:2]

    def unjudged_refusal(doc: Dict[str, Any]) -> None:
        doc["refusals"].append(
            {
                "code": "MODULE-DUPLICATE-ID",
                "subject": "probe",
                "detail": "probe",
                "source": "probe",
                "disposition": "recorded",
                "condition": "module-identity",
            }
        )

    def unknown_refusal_code(doc: Dict[str, Any]) -> None:
        doc["refusals"].append(
            {
                "code": "MODULE-NOT-IN-THE-VOCABULARY",
                "subject": "probe",
                "detail": "probe",
                "source": "probe",
                "disposition": "fatal",
                "condition": "module-identity",
            }
        )

    def broken_record(doc: Dict[str, Any]) -> None:
        doc["audit"]["records"][0]["disposition"] = "informational"

    def uncounted_state(doc: Dict[str, Any]) -> None:
        del doc["summary"]["not-a-module"]

    return [
        {"name": "an unknown state", "edit": unknown_state, "path": "modules/0/state"},
        {"name": "a missing field", "edit": missing_field, "path": "modules/0"},
        {"name": "an undeclared field", "edit": undeclared_field, "path": "modules/0"},
        {"name": "a truncated state list", "edit": second_state, "path": "states"},
        {
            "name": "a refusal filed as recorded",
            "edit": unjudged_refusal,
            "path": "refusals/0/disposition",
        },
        {
            "name": "a refusal code outside the vocabulary",
            "edit": unknown_refusal_code,
            "path": "refusals/0/code",
        },
        {"name": "an audit record with no such disposition", "edit": broken_record, "path": "audit/records/0/disposition"},
        {"name": "a state that is not counted", "edit": uncounted_state, "path": "summary"},
    ]


# --------------------------------------------------------------------------- #
# the artifact
# --------------------------------------------------------------------------- #
def test_the_packaged_schema_is_checkable() -> None:
    loaded = schema.load()
    assert loaded["$schema"] == schema.DIALECT
    assert loaded["$id"].endswith("module-registry.schema.json")
    schema.check_schema(loaded, schema.DEFAULT_SCHEMA)


def test_the_schema_freezes_the_refusal_vocabulary() -> None:
    """The code list is a contract in both directions, not a copy."""
    loaded = schema.load()
    assert sorted(loaded["$defs"]["refusalCode"]["enum"]) == sorted(REFUSAL_CODES)
    assert len(loaded["$defs"]["refusalCode"]["enum"]) == len(REFUSAL_CODES)


def test_the_schema_freezes_the_three_states_and_the_membership_refusal() -> None:
    loaded = schema.load()
    assert loaded["properties"]["states"]["minItems"] == loaded["properties"]["states"]["maxItems"] == 3
    assert loaded["properties"]["membership_refusal"]["const"] == "not-a-module"
    assert loaded["properties"]["membership_refusal"]["const"] not in loaded["$defs"]["state"]["enum"]


def test_the_schema_refuses_a_keyword_this_validator_cannot_enforce(tmp_path: Path) -> None:
    """An ignored keyword would be a requirement nobody measures."""
    data = schema.load()
    data["properties"]["modules"]["minLength"] = 1
    path = tmp_path / "unsupported.schema.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(schema.SchemaUnavailable) as excinfo:
        schema.load(path)
    assert "minLength" in str(excinfo.value)


def test_the_schema_refuses_a_dangling_reference(tmp_path: Path) -> None:
    data = schema.load()
    data["properties"]["hub"] = {"$ref": "#/$defs/noSuchDefinition"}
    path = tmp_path / "dangling.schema.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(schema.SchemaUnavailable) as excinfo:
        schema.load(path)
    assert "noSuchDefinition" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #
def test_the_clean_document_satisfies_the_frozen_schema(built) -> None:
    assert schema.problems(built, schema.load()) == ()


@pytest.mark.parametrize("case", mutants(), ids=[case["name"] for case in mutants()])
def test_a_malformed_row_is_rejected_at_its_path(built, case) -> None:
    doc = copy.deepcopy(built)
    case["edit"](doc)
    found = schema.problems(doc, schema.load())
    assert found, case["name"]
    assert any(problem.startswith("{}:".format(case["path"])) for problem in found), found


def test_a_rejection_names_the_row_it_refuses(built) -> None:
    doc = copy.deepcopy(built)
    doc["modules"][0]["state"] = "pending"
    found = schema.problems(doc, schema.load())
    assert any(problem.startswith("modules/0/state:") for problem in found), found
    # The path indexes the sorted module list, so the row is identifiable.
    assert doc["modules"][0]["id"] == sorted(entry["id"] for entry in doc["modules"])[0]


def test_a_missing_required_field_is_named(built) -> None:
    doc = copy.deepcopy(built)
    del doc["modules"][0]["health"]
    found = schema.problems(doc, schema.load())
    assert any("the required property 'health' is missing" in problem for problem in found), found


def test_the_generator_refuses_a_document_that_violates_the_schema(
    consumer, targets, make_hub, tmp_path
) -> None:
    """The generator validates what it emits, so the artifact is load bearing."""
    narrowed = schema.load()
    narrowed["properties"]["states"]["items"] = {"enum": ["registered-mandatory"]}
    path = tmp_path / "narrowed.schema.json"
    path.write_text(json.dumps(narrowed), encoding="utf-8")

    with pytest.raises(schema.SchemaViolation) as excinfo:
        registry.build(consumer, make_hub("hub-narrowed"), targets, schema_path=path)
    message = str(excinfo.value)
    assert "states" in message
    assert "frozen schema" in message

    # The same tree builds when the packaged schema applies — so the refusal is
    # the narrowed schema, not a defect in the tree.
    assert registry.build(consumer, make_hub("hub-plain"), targets)["states"]


def test_the_cli_reports_cannot_assess_for_an_unsatisfiable_schema(
    consumer, targets, make_hub, tmp_path, capsys
) -> None:
    from governance.modules.cli import EXIT_CANNOT_ASSESS, main

    narrowed = schema.load()
    narrowed["required"] = list(narrowed["required"]) + ["not_a_field"]
    path = tmp_path / "narrowed.schema.json"
    path.write_text(json.dumps(narrowed), encoding="utf-8")

    rc = main(
        [
            "verify",
            "--repo",
            str(consumer),
            "--hub",
            str(make_hub("hub-cli-narrowed")),
            "--targets",
            str(targets),
            "--schema",
            str(path),
        ]
    )
    assert rc == EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_the_validator_agrees_with_the_reference_implementation(built) -> None:
    """The subset validator is cross-checked, never trusted on its own word."""
    jsonschema = pytest.importorskip("jsonschema")
    reference = jsonschema.Draft202012Validator(schema.load())

    assert schema.problems(built, schema.load()) == ()
    assert not list(reference.iter_errors(built))

    for case in mutants():
        doc = copy.deepcopy(built)
        case["edit"](doc)
        mine = {problem.split(":", 1)[0] for problem in schema.problems(doc, schema.load())}
        theirs = {
            "/".join(str(part) for part in error.absolute_path)
            for error in reference.iter_errors(doc)
        }
        assert mine, case["name"]
        assert mine <= theirs, (case["name"], sorted(mine - theirs))
