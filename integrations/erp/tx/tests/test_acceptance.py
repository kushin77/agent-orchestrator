"""Acceptance criteria of issue #648, one test module per criterion.

The suite is organised so that each of the issue's four acceptance criteria has a
test that *fails when the criterion is not met*, rather than a test that merely
exercises the code the criterion mentions:

* criterion 1 — the golden flow posts a complete cycle with correct stock and GL
  effects, deterministically and without deriving a key;
* criterion 2 — cancellation reverses previous effects, and resubmitting a
  document is refused;
* criterion 3 — no duplicated constants: every definition resolves through the
  indexer or ERP-02;
* criterion 4 — negative controls, with coverage computed rather than claimed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Set

from integrations.erp.tx import definitions as definitions_module
from integrations.erp.tx import indexer, ledger, model, spine, stock

REPO_ROOT = Path(__file__).resolve().parents[4]
CATALOGUE = REPO_ROOT / "integrations" / "erp" / "catalog" / "documents"


# --- criterion 1: the golden flow -------------------------------------------


def test_golden_path_reports_no_findings(golden) -> None:
    assert golden.ok, [finding.to_dict() for finding in golden.findings]


def test_golden_path_posts_the_whole_cycle(golden) -> None:
    """Every family of the resolved cycle has a live, submitted document."""
    defs = golden.workspace.definitions
    for kind in defs.chain:
        documents = golden.workspace.of_kind(kind)
        assert documents, f"the cycle has no {kind} document"
        for document in documents:
            assert document.state != defs.initial_state(kind), (
                f"{document.id}: the cycle left a {kind} in its initial state"
            )


def test_golden_path_moves_stock_in_the_right_direction(golden, scene) -> None:
    """Five units leave the warehouse, and the ledger is 60.00 lighter of stock."""
    ledger_view = golden.workspace.stock
    item_code = scene.item["id"]
    warehouse = scene.warehouses[0]
    assert ledger_view.balance(item_code, warehouse) == -5.0
    assert ledger_view.value(item_code, warehouse) == -60.0


def test_golden_path_posts_a_balanced_ledger(golden) -> None:
    """Debits equal credits, and the receivable is the gross the invoice declared."""
    general = golden.workspace.ledger
    debits, credits = general.totals()
    assert debits == credits == 105.0
    assert general.balances() == {
        "AR-1100": 105.0,
        "REV-4000": -100.0,
        "TAX-PAYABLE": -5.0,
    }


def test_golden_path_is_keyless(golden, scene) -> None:
    """Every id on the workspace was supplied by the scenario, none was derived."""
    supplied: Set[str] = set(scene.ids)
    derived_ledger_ids = {
        document.id
        for document in golden.workspace.of_kind(
            golden.workspace.definitions.accounting_kind
        )
    }
    for document_id in golden.workspace.documents:
        assert document_id in supplied or document_id in derived_ledger_ids, (
            f"{document_id} was not supplied by the scenario and is not the ledger "
            "document derived from a supplied one"
        )
    # A derived id must be a function of a supplied one, not of a clock or a counter.
    for document_id in derived_ledger_ids:
        assert any(document_id.startswith(one) for one in supplied)


def test_golden_path_is_deterministic(defs, golden) -> None:
    """A second run reproduces the rail head, the balances and the stock."""
    again = spine.golden_path("again", defs)
    assert again.workspace.rail.head == golden.workspace.rail.head
    assert again.workspace.ledger.balances() == golden.workspace.ledger.balances()
    assert again.workspace.summary()["stock"] == golden.workspace.summary()["stock"]
    assert again.steps == golden.steps


def test_golden_path_records_every_step_on_the_rail(golden) -> None:
    """The rail verifies, and every document has at least one entry."""
    assert golden.workspace.rail.verify() == []
    for document_id in golden.workspace.documents:
        assert golden.workspace.rail.for_ref(document_id), (
            f"{document_id}: no audit step was recorded"
        )


# --- criterion 2: cancellation and resubmission -----------------------------


def test_cancellation_path_reports_no_findings(cancelled) -> None:
    assert cancelled.ok, [finding.to_dict() for finding in cancelled.findings]


def test_cancellation_returns_both_ledgers_to_zero(cancelled) -> None:
    """The balances return to their pre-cycle values, on the real accounts."""
    summary = cancelled.workspace.summary()
    assert summary["stock"] == {"ITEM-GOLDEN@WH-MAIN": 0.0}
    assert set(summary["ledgerBalances"]) == {"AR-1100", "REV-4000", "TAX-PAYABLE"}
    assert all(balance == 0.0 for balance in summary["ledgerBalances"].values())


def test_cancellation_reverses_rather_than_rolls_back(cancelled) -> None:
    """The ledgers still carry both the original effect and its reversal."""
    workspace = cancelled.workspace
    assert len(workspace.stock) >= 2, "the stock ledger was emptied rather than reversed"
    assert len(workspace.ledger) >= 6, "the ledger was emptied rather than reversed"
    assert [entry.reversal for entry in workspace.stock].count(True) >= 1
    assert [entry.reversal for entry in workspace.ledger].count(True) >= 3


def test_cancelled_documents_are_cancelled_in_state(cancelled, scene) -> None:
    """Every cycle document ends in the cancellation state its workflow declares."""
    workspace = cancelled.workspace
    for document_id in scene.ids:
        document = workspace.get(document_id)
        assert workspace.cancelled(document.kind, document.state), (
            f"{document_id} is in {document.state!r}, not cancelled"
        )


def test_resubmitting_a_document_is_refused(space, defs, scene) -> None:
    """Criterion 2's second half, provoked through the flow rather than the type."""
    at = spine.TIMELINE["quoted"]
    workspace = spine.draft(space, defs.chain[0], scene.ids[0], scene.bodies[0], at=at)
    workspace = spine.submit(workspace, scene.ids[0], at=at)
    try:
        spine.submit(workspace, scene.ids[0], at=at)
    except model.Refused as refusal:
        assert refusal.reason == "already-submitted"
        assert scene.ids[0] in refusal.detail
    else:
        raise AssertionError("a second submit of the same document was not refused")


# --- criterion 3: no duplicated constants -----------------------------------


def test_the_cycle_is_derived_from_the_erp02_schemas(defs) -> None:
    """The chain is what walking ERP-02's declared links produces, not a list here."""
    walked = [defs.chain[0]]
    while True:
        successor = next(
            (link.kind for link in defs.links if link.target == walked[-1]), None
        )
        if successor is None:
            break
        walked.append(successor)
    assert tuple(walked) == defs.chain
    assert defs.chain == ("quotation", "sales-order", "delivery-note", "sales-invoice")
    for link in defs.links:
        properties = defs.model.schema_for(link.kind).get("properties") or {}
        assert link.field in properties, (
            f"{link.kind}: the link field {link.field!r} is not declared by its schema"
        )


def test_lane_documents_match_the_indexer_catalogue(defs) -> None:
    """The lane's document set is read from the catalogue, not written down here."""
    declared = {
        payload["id"]
        for payload in (
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(CATALOGUE.glob("*.json"))
        )
        if payload.get("owning_issue") == definitions_module.LANE_ISSUE
    }
    assert declared, "the catalogue declares no document for this lane"
    assert set(defs.lane_ids()) == declared


def test_the_ledger_family_and_its_voucher_sources_are_derived(defs) -> None:
    """The posting family is the schema that declares a voucher_type, and no other."""
    declaring = tuple(
        kind
        for kind in defs.model.lifecycle_kinds()
        if "voucher_type" in (defs.model.schema_for(kind).get("properties") or {})
    )
    assert declaring == (defs.accounting_kind,)
    assert defs.chain[-1] in defs.voucher_sources


def test_the_package_declares_only_its_own_vocabularies(defs) -> None:
    """Its closed refusal and role vocabularies hold no ERP-02 state name.

    The spine's own actions deliberately include the words ERP-02 uses for its
    transitions (`submit`, `cancel`), because they record what the spine *did*;
    what must not be restated is the *state* vocabulary, which is resolved from
    the workflows instead.
    """
    erp_states = {
        state
        for kind in defs.model.lifecycle_kinds()
        for state in defs.model.workflow_for(kind).state_names()
    }
    assert not set(model.POSTING_ROLES) & erp_states
    assert not set(model.REFUSALS) & erp_states


def test_the_state_vocabulary_is_read_off_the_workflows(defs) -> None:
    """Every state the spine uses is the workflow's answer, recomputed here."""
    for kind in defs.addressable():
        workflow = defs.model.workflow_for(kind)
        assert defs.initial_state(kind) == workflow.initial
        cancelled = defs.cancellation_state(kind)
        assert workflow.state(cancelled).docstatus == 2
        submitted = defs.submitted_state(kind)
        assert workflow.state(submitted).docstatus == 1
        assert not workflow.state(submitted).terminal
        assert set(defs.terminal_states(kind)) == set(workflow.terminal_states())


def test_undriven_declarations_are_reported_with_a_reason(defs) -> None:
    """A declared document the spine does not drive is named, never silently dropped."""
    undriven = {document.id: reason for document, reason in defs.undriven()}
    assert set(undriven) | set(defs.addressable()) >= set(defs.lane_ids())
    for reason in undriven.values():
        assert reason
    assert "journal-entry" in undriven
    assert "payment-entry" in undriven


# --- criterion 4: negative controls -----------------------------------------


def test_every_refusal_is_provoked(defs) -> None:
    """The driver's provoked set equals the closed refusal vocabulary, exactly."""
    from integrations.erp.tx import negative_control

    import io

    sink = io.StringIO()
    assert negative_control.run(sink, definitions=defs) == 0, sink.getvalue()
    text = sink.getvalue()
    for code in model.REFUSALS:
        assert f"  OK    {code}:" in text, f"{code} was not provoked by name"


def test_the_refusal_vocabulary_is_closed() -> None:
    """A refuser cannot invent a code."""
    for code in model.REFUSALS:
        assert model.Refused(code, "detail").reason == code
    try:
        model.Refused("invented-code", "detail")
    except ValueError as error:
        assert "closed refusal vocabulary" in str(error)
    else:
        raise AssertionError("a code outside the vocabulary was accepted")


def test_the_spine_reads_the_indexer_rather_than_a_local_list(defs) -> None:
    """Criterion 3 at the seam: the lane set comes from the index, and only from it."""
    resolved = indexer.catalogue_documents(issue=definitions_module.LANE_ISSUE)
    assert tuple(document.id for document in resolved) == defs.lane_ids()
    for document in resolved:
        assert document.path.endswith(f"{document.id}.json")


def test_stock_and_ledger_vocabularies_stay_closed() -> None:
    """The two ledger modules refuse through :class:`~.model.Refused` only."""
    for module in (stock, ledger):
        assert getattr(module, "Refused", None) is model.Refused
