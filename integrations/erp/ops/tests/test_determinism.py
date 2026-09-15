"""Determinism, the audit rail, and the transcript the demo verb prints."""

from __future__ import annotations

import dataclasses
import json

from integrations.erp.ops import flows
from integrations.erp.ops.audit import GENESIS, Rail
from integrations.erp.ops.catalog import Catalog
from integrations.erp.ops.model import Model
from integrations.erp.ops.workspace import Workspace
from .helpers import assert_refused


def test_two_runs_of_the_golden_path_are_identical(model: Model, catalog: Catalog) -> None:
    assert flows.digest(flows.golden_path(model, catalog)) == flows.digest(
        flows.golden_path(model, catalog)
    )


def test_the_transcript_is_json_round_trippable(model: Model, catalog: Catalog) -> None:
    transcript = flows.golden_path(model, catalog)
    assert json.loads(flows.render(transcript))["issue"] == 649


def test_the_transcript_carries_the_flag_it_belongs_to(
    model: Model, catalog: Catalog
) -> None:
    transcript = flows.golden_path(model, catalog)
    assert transcript["flag"] == {"id": "erp-module", "default": "off"}


def test_the_books_tie_out_across_the_whole_run(model: Model, catalog: Catalog) -> None:
    transcript = flows.golden_path(model, catalog)
    accounts = transcript["workspace"]["accounts"]
    assert accounts, "the run posted nothing"
    assert round(sum(accounts.values()), 2) == 0.0
    # Work In Process is a way-station between the two manufacturing postings, so
    # a finished run leaves nothing parked there.
    assert "WORK-IN-PROCESS" not in accounts


def test_the_run_produces_both_cycles(model: Model, catalog: Catalog) -> None:
    transcript = flows.golden_path(model, catalog)
    assert transcript["purchase_cycle"]["order"] == [
        "rfq",
        "purchase-order",
        "purchase-receipt",
        "purchase-invoice",
    ]
    assert transcript["manufacturing"]["boms"] == ["BOM-FG-1", "BOM-SUB-1"]
    assert len(transcript["manufacturing"]["work_orders"]) == 2
    assert sorted(transcript["manufacturing"]["completed"]) == transcript[
        "manufacturing"
    ]["work_orders"]


def test_the_explosions_in_the_transcript_are_multi_level(
    model: Model, catalog: Catalog
) -> None:
    transcript = flows.golden_path(model, catalog)
    assert transcript["manufacturing"]["explosions"]["BOM-FG-1"] == {
        "RAW-A": 2.0,
        "RAW-B": 4.0,
    }


def test_the_finished_good_lands_in_the_finished_warehouse(
    model: Model, catalog: Catalog
) -> None:
    transcript = flows.golden_path(model, catalog)
    stock = transcript["workspace"]["stock"]
    assert stock[flows.FINISHED_WAREHOUSE]["FG-1"] == 4.0
    # Ten sub-assemblies were produced and four consumed as sub-assemblies, so
    # six remain: the build consumes the sub-assembly, not its raw material.
    assert stock[flows.FINISHED_WAREHOUSE]["SUB-1"] == 6.0


def test_the_rail_re_derives(model: Model, catalog: Catalog) -> None:
    space = flows.workspace(model, catalog)
    space.create(
        {
            "doctype": "item",
            "id": "RAW-NEW",
            "name": "A new raw material",
            "uom": "Kg",
            "is_stock_item": True,
        },
        at=flows.T["masters"],
        actor="test",
    )
    assert space.rail.verify() == ()
    assert space.rail.head() != GENESIS


def test_a_tampered_rail_reports_every_break(model: Model, catalog: Catalog) -> None:
    """An entry edited in place no longer re-derives, and the chain says so."""
    rail = flows.workspace(model, catalog).rail
    assert len(rail) > 1
    tampered = Rail(entries=(dataclasses.replace(rail.entries[0], actor="someone"),))
    findings = tampered.verify()
    assert findings
    assert all(finding.code == "audit-broken" for finding in findings)
    assert "does not re-derive" in findings[0].detail


def test_the_rail_refuses_an_action_outside_its_vocabulary() -> None:
    assert_refused(
        lambda: Rail().append(
            at="2026-09-15T00:00:00Z", actor="a", action="launch", document="X-1"
        ),
        "unknown-action",
        needle="launch",
    )


def test_the_rail_refuses_an_entry_with_no_document() -> None:
    assert_refused(
        lambda: Rail().append(
            at="2026-09-15T00:00:00Z", actor="a", action="create", document=""
        ),
        "invalid-value",
        needle="must name a document",
    )


def test_the_workspace_refuses_a_duplicate_id(space: Workspace) -> None:
    assert_refused(
        lambda: space.create(
            dict(flows.ITEMS[0]), at=flows.T["masters"], actor="test"
        ),
        "duplicate-id",
        needle="RAW-A",
    )


def test_the_workspace_refuses_an_unknown_document(space: Workspace) -> None:
    assert_refused(lambda: space.get("X-1"), "unknown-document", needle="X-1")
