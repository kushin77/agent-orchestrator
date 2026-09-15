"""Integration controls for the spine's flows, and the controls that can fail."""

from __future__ import annotations

import io

import pytest

from integrations.erp.tx import audit, model, negative_control, spine, stock

AT = spine.TIMELINE["quoted"]


def _ready(defs, scene):
    """A workspace with the cycle's first document submitted."""
    space = spine.workspace(defs, scene)
    space = spine.draft(space, defs.chain[0], scene.ids[0], scene.bodies[0], at=AT)
    return spine.submit(space, scene.ids[0], at=AT)


def test_draft_refuses_a_reserved_field(defs, scene, space) -> None:
    with pytest.raises(model.Refused) as caught:
        spine.draft(
            space, defs.chain[0], scene.ids[0], {**scene.bodies[0], "docstatus": 2}, at=AT
        )
    assert caught.value.reason == "invalid-value"
    assert "docstatus" in caught.value.detail


def test_draft_refuses_a_duplicate_id(defs, scene, space) -> None:
    space = spine.draft(space, defs.chain[0], scene.ids[0], scene.bodies[0], at=AT)
    with pytest.raises(model.Refused) as caught:
        spine.draft(space, defs.chain[0], scene.ids[0], scene.bodies[0], at=AT)
    assert caught.value.reason == "duplicate-id"


def test_draft_holds_the_body_to_the_erp02_schema(defs, scene, space) -> None:
    with pytest.raises(model.Refused) as caught:
        spine.draft(space, defs.chain[0], scene.ids[0], {"party": "CUST-1"}, at=AT)
    assert caught.value.reason == "schema-violation"


def test_a_delivery_cannot_precede_its_order(defs, scene, space) -> None:
    space = _ready(defs, scene)
    space = spine.raise_document(
        space, defs.chain[1], scene.ids[1], scene.ids[0], scene.bodies[1], at=AT
    )
    with pytest.raises(model.Refused) as caught:
        spine.deliver(space, scene.ids[2], scene.ids[1], scene.bodies[2], at=AT)
    assert caught.value.reason == "not-submitted"
    assert scene.ids[1] in caught.value.detail


def test_a_delivery_may_not_exceed_the_order(defs, scene, space) -> None:
    space = _ready(defs, scene)
    space = spine.raise_document(
        space, defs.chain[1], scene.ids[1], scene.ids[0], scene.bodies[1], at=AT
    )
    space = spine.submit(space, scene.ids[1], at=AT)
    over = [{"item_code": scene.item["id"], "qty": 6, "rate": 20.0}]
    with pytest.raises(model.Refused) as caught:
        spine.deliver(
            space, scene.ids[2], scene.ids[1], {**scene.bodies[2], "lines": over}, at=AT
        )
    assert caught.value.reason == "over-delivery"


def test_an_invoice_may_not_exceed_the_delivery(defs, scene, space) -> None:
    space = _ready(defs, scene)
    space = spine.raise_document(
        space, defs.chain[1], scene.ids[1], scene.ids[0], scene.bodies[1], at=AT
    )
    space = spine.submit(space, scene.ids[1], at=AT)
    space = spine.deliver(space, scene.ids[2], scene.ids[1], scene.bodies[2], at=AT)
    over = [{"item_code": scene.item["id"], "qty": 6, "rate": 20.0}]
    with pytest.raises(model.Refused) as caught:
        spine.invoice(
            space, scene.ids[3], scene.ids[2], {**scene.bodies[3], "lines": over}, at=AT
        )
    assert caught.value.reason == "over-invoice"


def test_a_document_raised_against_the_wrong_family_is_refused(defs, scene, space) -> None:
    space = _ready(defs, scene)
    with pytest.raises(model.Refused) as caught:
        spine.raise_document(
            space, defs.chain[2], scene.ids[2], scene.ids[0], scene.bodies[2], at=AT
        )
    assert caught.value.reason == "wrong-document"


def test_a_link_the_scenario_sets_differently_is_refused(defs, scene, space) -> None:
    space = _ready(defs, scene)
    field_name = defs.link_field(defs.chain[1])
    with pytest.raises(model.Refused) as caught:
        spine.raise_document(
            space,
            defs.chain[1],
            scene.ids[1],
            scene.ids[0],
            {**scene.bodies[1], field_name: "SOMETHING-ELSE"},
            at=AT,
        )
    assert caught.value.reason == "invalid-value"
    assert field_name in caught.value.detail


def test_a_cancellation_with_a_live_dependant_is_refused(defs, scene, space) -> None:
    space = _ready(defs, scene)
    space = spine.raise_document(
        space, defs.chain[1], scene.ids[1], scene.ids[0], scene.bodies[1], at=AT
    )
    space = spine.submit(space, scene.ids[1], at=AT)
    space = spine.deliver(space, scene.ids[2], scene.ids[1], scene.bodies[2], at=AT)
    with pytest.raises(model.Refused) as caught:
        spine.cancel(space, scene.ids[1], at=AT)
    assert caught.value.reason == "live-dependant"
    assert scene.ids[2] in caught.value.detail


def test_a_live_order_blocks_the_quotation_it_was_raised_from(defs, scene, space) -> None:
    space = _ready(defs, scene)
    space = spine.raise_document(
        space, defs.chain[1], scene.ids[1], scene.ids[0], scene.bodies[1], at=AT
    )
    with pytest.raises(model.Refused) as caught:
        spine.cancel(space, scene.ids[0], at=AT)
    assert caught.value.reason == "live-dependant"


def test_cancelling_frees_the_quantity_for_a_later_delivery(defs, scene, space) -> None:
    """The quantity is measured over live documents, so a cancellation frees it."""
    space = _ready(defs, scene)
    space = spine.raise_document(
        space, defs.chain[1], scene.ids[1], scene.ids[0], scene.bodies[1], at=AT
    )
    space = spine.submit(space, scene.ids[1], at=AT)
    space = spine.deliver(space, scene.ids[2], scene.ids[1], scene.bodies[2], at=AT)
    space = spine.cancel(space, scene.ids[2], at=AT)
    space = spine.deliver(space, "DN-2", scene.ids[1], scene.bodies[2], at=AT)
    assert space.stock.balance(scene.item["id"], scene.warehouses[0]) == -5.0


def test_the_ledger_document_is_validated_by_erp02(defs, scene, space) -> None:
    """A posting this package believes balanced still has to survive ERP-02."""
    kind = defs.accounting_kind
    posting = {
        "doctype": kind,
        "id": "GL-1",
        "state": defs.initial_state(kind),
        "docstatus": 0,
        "company": "CO",
        "currency": "USD",
        "posting_date": "2026-03-06",
        "voucher_type": defs.chain[-1],
        "voucher_id": scene.ids[3],
        "lines": [{"account": "AR", "debit": 10.0}, {"account": "REV", "credit": 9.0}],
    }
    with pytest.raises(model.Refused) as caught:
        defs.validate(kind, posting)
    assert caught.value.reason == "unbalanced-posting", (
        "ERP-02's own double-entry rule must refuse the posting"
    )


def test_a_posting_naming_a_family_that_is_not_a_voucher_source_is_refused(
    defs, scene, space
) -> None:
    kind = defs.accounting_kind
    posting = {
        "doctype": kind,
        "id": "GL-2",
        "state": defs.initial_state(kind),
        "docstatus": 0,
        "company": "CO",
        "currency": "USD",
        "posting_date": "2026-03-06",
        "voucher_type": "delivery-note",
        "voucher_id": scene.ids[2],
        "lines": [{"account": "AR", "debit": 10.0}, {"account": "REV", "credit": 10.0}],
    }
    with pytest.raises(model.Refused):
        defs.validate(kind, posting)


def test_an_unknown_document_is_refused(space) -> None:
    with pytest.raises(model.Refused) as caught:
        space.get("NOPE")
    assert caught.value.reason == "unknown-document"
    assert "NOPE" in caught.value.detail


def test_the_workspace_reports_its_own_findings(golden) -> None:
    assert golden.workspace.findings() == []


def test_the_summary_is_a_measurement_of_the_ledgers(golden, scene) -> None:
    summary = golden.workspace.summary()
    assert summary["documents"][scene.ids[2]]["state"] == "submitted"
    assert summary["ledgerTotals"] == {"debits": 105.0, "credits": 105.0}
    assert summary["railHead"] == golden.workspace.rail.head


# --- the driver can fail ----------------------------------------------------


def test_the_negative_control_driver_fails_when_the_reversal_is_neutered(
    defs, monkeypatch
) -> None:
    """The gate's mutation, applied in-process: the driver must notice."""
    monkeypatch.setattr(stock.Movement, "inverted", lambda self, *, at: self)
    sink = io.StringIO()
    assert negative_control.run(sink, definitions=defs) == 1
    assert "after cancellation" in sink.getvalue()


def test_the_negative_control_driver_fails_when_a_refusal_cannot_fire(
    defs, monkeypatch
) -> None:
    """A control whose legality check is neutered must turn the driver red."""
    monkeypatch.setattr(spine, "cancel", lambda space, document_id, *, at, actor=None: space)
    sink = io.StringIO()
    assert negative_control.run(sink, definitions=defs) == 1
    assert "NOT-OK" in sink.getvalue()


def test_the_negative_control_driver_fails_when_coverage_diverges(
    defs, monkeypatch
) -> None:
    """Coverage is computed: a code nothing provokes must fail the driver."""
    monkeypatch.setattr(
        negative_control, "REFUSALS", model.REFUSALS | {"invented-code"}
    )
    sink = io.StringIO()
    assert negative_control.run(sink, definitions=defs) == 1
    text = sink.getvalue()
    assert "invented-code" in text
    assert "no control provokes it" in text


def test_the_audit_action_vocabulary_is_the_spines_own(golden) -> None:
    recorded = {entry.action for entry in golden.workspace.rail}
    assert recorded <= set(model.ACTIONS)
    assert audit.DOMAIN.startswith("ao-erp-tx")
