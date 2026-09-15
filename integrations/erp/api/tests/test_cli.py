"""The lane's CLI: the tri-state contract and the golden path (#651).

``check`` is the verb the gate names, so its exit code has to mean something: OK
only when every invariant was measured and held, CANNOT-ASSESS when the model or a
declaration cannot be read — and **never** OK in that case. ``demo`` exists so the
determinism of the whole surface is visible rather than asserted: the same request
sequence, twice, with the same result.
"""

from __future__ import annotations

import json

import pytest

from integrations.erp.api import cli, fixtures
from integrations.erp.core.errors import ErpError, workflow_invalid


def test_check_is_ok():
    assert cli.main(["check"]) == cli.OK


def test_controls_is_ok():
    assert cli.main(["controls"]) == cli.OK


def test_emit_prints_the_document_to_stdout(capsys):
    assert cli.main(["emit"]) == cli.OK
    printed = capsys.readouterr().out
    document = json.loads(printed)
    assert document["openapi"] == "3.1.0"
    assert document["info"]["title"] == "ERP document API"


def test_emit_writes_the_same_bytes_a_fresh_emission_produces(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert cli.main(["emit", "--out", str(first)]) == cli.OK
    assert cli.main(["emit", "--out", str(second)]) == cli.OK
    assert first.read_bytes() == second.read_bytes()
    assert (fixtures.repository_root() / "integrations/erp/api/openapi.json").read_bytes() == (
        first.read_bytes()
    ), "a fresh emission must equal the committed artifact"


def test_routes_prints_the_derivation(capsys):
    assert cli.main(["routes"]) == cli.OK
    printed = json.loads(capsys.readouterr().out)
    assert [route["operationId"] for route in printed["routes"]][:2] == [
        "erp.documents.list",
        "erp.documents.create",
    ]
    assert printed["kinds"] == list(fixtures.model().document_kinds())
    assert printed["declarations"]["roles"] == sorted(fixtures.ROLE_ACTIONS)


def test_demo_is_deterministic(capsys):
    assert cli.main(["demo"]) == cli.OK
    printed = json.loads(capsys.readouterr().out)
    assert printed["deterministic"] is True
    assert printed["first"]["digest"] == printed["second"]["digest"]


def test_the_golden_path_exercises_every_verb():
    transcript = cli.golden_path()
    paths = [step["request"]["path"] for step in transcript["steps"]]
    assert any("transitions/" in path for path in paths)
    assert any(step["code"] == "document_not_found" for step in transcript["steps"]), (
        "the golden path must include a refusal, or it only demonstrates the happy path"
    )
    assert all("requestId" in step for step in transcript["steps"])
    # The two platform routes answer without a decision, and are exercised too.
    assert any(step["request"]["path"].endswith("/health") for step in transcript["steps"])


def test_the_golden_path_projects_by_omission():
    """A read in the transcript withholds `po_reference` and says so."""
    transcript = cli.golden_path()
    reads = [step for step in transcript["steps"] if step["request"]["path"].endswith("SALES-ORDER-0001")]
    assert reads, "the transcript reads one sales order"
    document = reads[0]["data"]["document"]
    assert "po_reference" not in document
    assert reads[0]["data"]["redacted"] == ["po_reference"]


def test_the_golden_path_is_the_same_for_another_role():
    """The path is driven with a non-default role too, so the rig is not role-shaped."""
    transcript = cli.golden_path("ERP Auditor")
    assert transcript["role"] == "ERP Auditor"
    # An auditor may read and may not delete: the transcript records the refusal.
    assert any(step["code"] == "forbidden" for step in transcript["steps"])


def test_the_role_flag_is_accepted():
    assert cli.main(["--role", "ERP Manager", "routes"]) == cli.OK


def test_a_model_that_will_not_load_is_cannot_assess(monkeypatch):
    """Never OK: a lane that cannot read its model cannot report on it."""
    def boom():
        raise workflow_invalid("provoked: the model will not load")

    monkeypatch.setattr(cli.fixtures, "model", boom)
    assert cli.main(["check"]) == cli.CANNOT_ASSESS


def test_a_declaration_that_will_not_load_is_cannot_assess(monkeypatch):
    """The same rule for the declarations: un-assessable is its own answer."""
    def boom(*_args, **_kwargs):
        raise ErpError(503, "declaration-invalid", "provoked: unreadable", None)

    monkeypatch.setattr(cli.fixtures, "declarations", boom)
    assert cli.main(["check"]) == cli.CANNOT_ASSESS


def test_the_tri_state_values_are_distinct():
    assert (cli.OK, cli.NOT_OK, cli.CANNOT_ASSESS) == (0, 1, 2)


def test_check_reports_a_hand_edited_artifact(monkeypatch, capsys):
    """The staleness check is real: a tampered artifact turns `check` red, by name.

    The artifact is tampered with *as it is read*, not by patching the emitter —
    patching both sides of the comparison would leave it equal and the test would
    pass while proving nothing.
    """
    real_read = cli.Path.read_text

    def hand_edited(self, *args, **kwargs):
        text = real_read(self, *args, **kwargs)
        if self.name == "openapi.json":
            return text.replace('"1.0.0"', '"9.9.9"')
        return text

    monkeypatch.setattr(cli.Path, "read_text", hand_edited)
    assert cli.main(["check"]) == cli.NOT_OK
    assert "stale or hand-edited" in capsys.readouterr().err


def test_check_reports_a_document_that_no_longer_matches_its_sources(monkeypatch, capsys):
    """And the drift check is real too: a drifted component fails by name."""
    real_build = cli.openapi_module.build_document

    def drifted(root):
        document = real_build(root)
        document["components"]["schemas"]["sales-order"]["properties"]["total"]["minimum"] = 1
        return document

    monkeypatch.setattr(cli.openapi_module, "build_document", drifted)
    assert cli.main(["check"]) == cli.NOT_OK
    assert "drifts from its ERP-02 source" in capsys.readouterr().err


def test_check_reports_a_hidden_back_door(monkeypatch, capsys):
    """A second authorization call site fails the check — 'no back door', measured."""
    real_read = cli.Path.read_text

    def with_back_door(self, *args, **kwargs):
        text = real_read(self, *args, **kwargs)
        if self.name == "surface.py":
            return text + "\n# auth_scope.authorize(  # a second call site\n"
        return text

    monkeypatch.setattr(cli.Path, "read_text", with_back_door)
    assert cli.main(["check"]) == cli.NOT_OK
    assert "calls the authorization layer 2 time(s)" in capsys.readouterr().err
