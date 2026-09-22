"""The emitted brief is validated against the frozen schema (issue #592).

The composer emits a machine document and validates it against
``brief.schema.json`` on every run. These tests fail if the schema stops
validating, if the composer stops validating against it, or if a document that
is no longer the frozen brief is accepted.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from governance.modules.model import CannotAssess, TARGET_PENDING

from integrations.paperclip.reporting import brief_schema

SCHEMA_FILE = "brief.schema.json"
PACKAGE = "integrations/paperclip/reporting"


def run_cli(tree: Path, *args: str) -> "subprocess.CompletedProcess":
    """The scratch tree's own CLI, in a fresh interpreter."""
    return subprocess.run(
        [
            sys.executable,
            str(tree / PACKAGE / "cli.py"),
            *args,
            "--repo",
            str(tree),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _codes(findings) -> set:
    return {finding.code for finding in findings}


def test_the_shipped_composition_emits_a_document_that_satisfies_the_schema(composition):
    assert composition.document, "the composition emitted no machine document"
    assert brief_schema.validate(composition.document, brief_schema.load()) == ()
    assert not [f for f in composition.findings if f.code == "BRIEF-SCHEMA-INVALID"]


def test_the_document_carries_every_field_the_issue_freezes(composition):
    document = composition.document
    assert document["schema"] == "ao.module-brief/v1"
    assert document["states"] == [
        "registered-mandatory",
        "target-pending",
        "catalog-module-not-mandatory",
    ]
    assert document["claims"], "the document carries no claim list"
    assert document["modules"], "the document carries no module"
    for record in document["modules"]:
        for field in (
            "id",
            "state",
            "owning_repo",
            "mandatory",
            "shipped",
            "pin",
            "pin_present",
            "rev",
            "rev_present",
            "consumer_assets",
            "assets",
            "health",
            "board_ref",
            "blocking",
            "drift",
        ):
            assert field in record, (record["id"], field)
        for asset in record["assets"]:
            assert set(asset) == {"asset", "seed", "seed_present"}


def test_a_pending_module_is_recorded_as_unshipped_with_its_blocker(pending_composition):
    pending = [
        record
        for record in pending_composition.document["modules"]
        if record["state"] == TARGET_PENDING
    ]
    assert pending, "the pending venue carries no pending target to record"
    for record in pending:
        assert record["shipped"] is False
        assert record["mandatory"] is None
        assert record["pin"] == "" and record["pin_present"] is False
        assert record["blocking"], record["id"]


def test_the_claim_list_is_the_rendered_claim_list(composition):
    assert [c["line"] for c in composition.document["claims"]] == [
        claim.line for claim in composition.claims
    ]
    for record in composition.document["claims"]:
        assert record["citations"], record


def test_a_document_with_a_fourth_state_is_refused_by_name(composition):
    doctored = copy.deepcopy(composition.document)
    doctored["modules"][0]["state"] = "probably-fine"
    findings = brief_schema.validate(doctored, brief_schema.load())
    assert findings, "a fourth state was accepted"
    assert _codes(findings) == {"BRIEF-SCHEMA-INVALID"}
    assert "probably-fine" in findings[0].render() or ".state" in findings[0].subject


def test_a_document_missing_a_frozen_field_is_refused_by_name(composition):
    doctored = copy.deepcopy(composition.document)
    del doctored["modules"][0]["drift"]
    findings = brief_schema.validate(doctored, brief_schema.load())
    assert [f for f in findings if "drift" in f.render()], findings
    assert _codes(findings) == {"BRIEF-SCHEMA-INVALID"}


def test_a_document_with_an_unexpected_field_is_refused_by_name(composition):
    doctored = copy.deepcopy(composition.document)
    doctored["modules"][0]["confidence"] = "high"
    findings = brief_schema.validate(doctored, brief_schema.load())
    assert [f for f in findings if "confidence" in f.render()], findings


def test_a_frozen_field_a_claim_lacks_is_refused_by_name(composition):
    """The schema freezes the claim list's shape: the field must be there, typed.

    Emptiness is not expressible here (the validator subset has no ``minItems``),
    and it does not need to be: a claim that cites nothing is refused by
    ``claim_findings`` **naming the line** — see ``test_claims.py``, where the
    uncited claim is provoked through the vocabulary rather than the schema.
    """
    doctored = copy.deepcopy(composition.document)
    del doctored["claims"][0]["citations"]
    findings = brief_schema.validate(doctored, brief_schema.load())
    assert findings, "a claim missing its citation list was accepted"
    assert _codes(findings) == {"BRIEF-SCHEMA-INVALID"}
    assert any("citations" in finding.render() for finding in findings), findings


def test_the_composer_refuses_a_brief_that_breaks_the_schema(tree: Path):
    """The composer validates what it emits: doctor the schema and it must refuse.

    Run through the scratch tree's own CLI, because the composer loads the schema
    from *its* package: doctoring the lane's own copy would prove nothing.
    """
    schema_path = tree / PACKAGE / SCHEMA_FILE
    raw_before = schema_path.read_text(encoding="utf-8")
    schema = json.loads(raw_before)
    required = list(schema["properties"]["summary"]["required"])
    schema["properties"]["summary"]["required"] = required + ["refused-names"]
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    assert schema_path.read_text(encoding="utf-8") != raw_before, "the mutation did not land"

    proc = run_cli(tree, "compose")
    assert proc.returncode == 1, proc.stderr
    assert "BRIEF-SCHEMA-INVALID:" in proc.stderr
    assert "refused-names" in proc.stderr


def test_the_schema_uses_only_keywords_the_validator_implements():
    """A `$ref` or a `oneOf` would validate nothing at all — so it is refused here."""
    schema = brief_schema.load()
    unsupported = brief_schema.unsupported_keywords(schema)
    assert unsupported == (), unsupported
    for keyword in ("$ref", "oneOf", "anyOf", "allOf", "not", "const", "definitions"):
        assert keyword not in brief_schema.VALIDATION_KEYWORDS
        assert keyword not in brief_schema.METADATA_KEYWORDS


def test_the_schema_is_the_frozen_one_and_a_foreign_one_is_cannot_assess(tree: Path):
    schema_path = tree / PACKAGE / SCHEMA_FILE
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["$id"] = "ao.module-brief/v2"
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(CannotAssess):
        brief_schema.load(tree / PACKAGE)


def test_a_missing_schema_is_cannot_assess_never_a_pass(tree: Path):
    (tree / PACKAGE / SCHEMA_FILE).unlink()
    with pytest.raises(CannotAssess):
        brief_schema.load(tree / PACKAGE)
