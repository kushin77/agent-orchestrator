"""CRUD over the document families, offline and deterministic (#651, criterion 2).

Criterion 2 is *"CRUD over the document families works offline with deterministic
fixtures"*. So every verb is exercised over **every kind the model declares** —
not one representative family — and the refusals that make CRUD mean something
(absent, conflicting, undeclared move) are provoked too. Nothing here reaches the
network, the clock or the shared checkout.
"""

from __future__ import annotations

import copy

import pytest

from integrations.erp.api import fixtures


def _collection(kind: str) -> str:
    return f"/v1/erp/documents/{kind}"


def _item(kind: str, document_id: str) -> str:
    return f"/v1/erp/documents/{kind}/{document_id}"


ALL_KINDS = sorted(fixtures.documents())


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_list_and_get_every_kind(world, kind):
    document = fixtures.documents()[kind][0]
    listed = world.get(_collection(kind))
    assert listed["ok"] and listed["status"] == 200
    assert listed["data"]["kind"] == kind
    assert listed["data"]["count"] >= 1
    assert document["id"] in [item["id"] for item in listed["data"]["items"]]

    fetched = world.get(_item(kind, document["id"]))
    assert fetched["ok"] and fetched["status"] == 200
    assert fetched["data"]["document"]["id"] == document["id"]
    assert fetched["data"]["document"]["doctype"] == kind


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_create_then_delete_every_kind(world, kind):
    fresh = copy.deepcopy(fixtures.documents()[kind][0])
    fresh["id"] = f"{fresh['id']}-NEW"
    created = world.post(_collection(kind), fresh)
    assert created["status"] == 201, created
    assert created["data"]["document"]["id"] == fresh["id"]

    # It is really there, and the list grew.
    assert world.get(_item(kind, fresh["id"]))["ok"]

    removed = world.delete(_item(kind, fresh["id"]))
    assert removed["ok"] and removed["status"] == 200
    assert removed["data"]["deleted"] == {"kind": kind, "id": fresh["id"]}
    assert world.get(_item(kind, fresh["id"]))["status"] == 404


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_replace_every_kind(world, kind):
    original = fixtures.documents()[kind][0]
    replacement = copy.deepcopy(original)
    # A change that no family rule can object to: a longer label on `name`, when
    # the family has one at all.
    if "name" in replacement:
        replacement["name"] = "Northwind Trading Co"
    replaced = world.put(_item(kind, original["id"]), replacement)
    assert replaced["ok"] and replaced["status"] == 200
    assert replaced["data"]["document"]["id"] == original["id"]


def test_every_family_validates_against_the_model(model):
    """The offline corpus is the model's — a fixture the API would refuse is not evidence."""
    for kind in ALL_KINDS:
        for document in fixtures.documents()[kind]:
            assert model.validate_document(kind, document)["doctype"] == kind


def test_a_document_of_another_kind_is_refused_by_the_model(world):
    """The family's pinned `doctype` is what refuses a cross-family payload."""
    payload = fixtures.documents()["party"][0]
    refused = world.post(_collection("item"), payload)
    assert refused["status"] == 400
    assert refused["error"]["code"] == "schema_violation"


def test_a_missing_required_field_is_refused_by_the_model(world):
    refused = world.post(_collection("party"), {"doctype": "party", "id": "CUST-8000"})
    assert refused["status"] == 400
    assert refused["error"]["code"] == "schema_violation"
    assert "schema violation" in refused["error"]["message"]
    assert refused["error"]["details"]["problems"], "the model's own violations must travel"


def test_an_absent_document_is_a_404(world):
    refused = world.get(_item("sales-order", "SALES-ORDER-9999"))
    assert refused["status"] == 404
    assert refused["error"]["code"] == "document_not_found"
    assert "SALES-ORDER-9999" in refused["error"]["message"]


def test_a_duplicate_id_is_a_conflict(world):
    payload = fixtures.documents()["party"][0]
    first = world.post(_collection("party"), {**payload, "id": "CUST-7000"})
    assert first["status"] == 201
    second = world.post(_collection("party"), {**payload, "id": "CUST-7000"})
    assert second["status"] == 409
    assert second["error"]["code"] == "conflict"


def test_a_replace_may_not_re_key_a_document(world):
    payload = fixtures.documents()["party"][0]
    refused = world.put(_item("party", "CUST-0001"), {**payload, "id": "CUST-5555"})
    assert refused["status"] == 400
    assert refused["error"]["code"] == "invalid_body"
    assert "re-key" in refused["error"]["message"]


def test_a_declared_transition_advances_a_document(world):
    before = world.get(_item("sales-order", "SALES-ORDER-0001"))["data"]["document"]
    assert before["state"] == "draft" and before["docstatus"] == 0

    moved = world.post(f"{_item('sales-order', 'SALES-ORDER-0001')}/transitions/submit")
    assert moved["ok"] and moved["status"] == 200
    # The reply reports the move: identity, the state reached, and the action.
    assert moved["data"]["action"] == "submit"
    assert moved["data"]["transition"] == {
        "doctype": "sales-order",
        "id": "SALES-ORDER-0001",
        "state": "submitted",
        # The docstatus is re-derived from the target state — ERP-02's parity, kept
        # by the store rather than restated by the surface.
        "docstatus": 1,
    }
    assert "document" not in moved["data"], (
        "a transition carries no fields, so it must not answer with a field set"
    )

    after = world.get(_item("sales-order", "SALES-ORDER-0001"))["data"]["document"]
    assert after["state"] == "submitted" and after["docstatus"] == 1


def test_an_undeclared_move_is_refused_by_the_workflow(world):
    refused = world.post(f"{_item('sales-order', 'SALES-ORDER-0001')}/transitions/complete")
    assert refused["status"] == 409
    assert refused["error"]["code"] == "unknown_action"
    assert "unknown_action" not in refused["error"]["message"]  # the message names the move
    assert "complete" in refused["error"]["message"]


def test_the_transition_reply_carries_no_field_a_read_would_withhold(world):
    """The transition reply is identity and state only — it cannot leak a withheld field."""
    moved = world.post(f"{_item('sales-order', 'SALES-ORDER-0001')}/transitions/submit")
    assert set(moved["data"]) == {"transition", "action"}
    assert set(moved["data"]["transition"]) == {"doctype", "id", "state", "docstatus"}


def test_a_kind_without_a_workflow_has_no_transition(world):
    refused = world.post(f"{_item('party', 'CUST-0001')}/transitions/submit")
    assert refused["status"] == 404
    assert refused["error"]["code"] == "unknown_workflow"


def test_a_family_rule_is_enforced_through_the_surface(world):
    """A GL posting whose debits do not equal its credits never reaches the store."""
    unbalanced = {
        **fixtures.documents()["gl-posting"][0],
        "id": "GL-POSTING-7000",
        "lines": [
            {"account": "DEBTORS", "debit": 100.0},
            {"account": "REVENUE", "credit": 60.0},
        ],
    }
    refused = world.post(_collection("gl-posting"), unbalanced)
    assert refused["status"] == 422
    assert refused["error"]["code"] == "unbalanced_posting"
    assert "100.00" in refused["error"]["message"] and "60.00" in refused["error"]["message"]


def test_a_stock_transfer_that_moves_nothing_is_refused(world):
    transfer = {
        **fixtures.documents()["stock-entry"][0],
        "id": "STOCK-ENTRY-7000",
        "purpose": "material_transfer",
        "from_warehouse": "MAIN",
        "to_warehouse": "MAIN",
    }
    refused = world.post(_collection("stock-entry"), transfer)
    assert refused["status"] == 422
    assert refused["error"]["code"] == "invalid_transfer"


# --- the route boundary -----------------------------------------------------


def test_an_unknown_route_is_a_404(world):
    """Criterion 4's second half: an unknown route 404s with the documented shape."""
    refused = world.get("/v1/erp/sprockets")
    assert refused["status"] == 404
    assert refused["error"]["code"] == "not_found"
    assert refused["error"]["code"] in err_codes()
    assert refused["data"] is None
    assert refused["ok"] is False
    assert refused["requestId"]


def test_an_unknown_kind_is_a_404_naming_the_kind(world):
    refused = world.get(_collection("sprocket"))
    assert refused["status"] == 404
    assert refused["error"]["code"] == "unknown_document_kind"
    assert "sprocket" in refused["error"]["message"]
    assert refused["error"]["details"]["known"], "the refusal names the kinds there are"


def test_a_known_path_with_the_wrong_method_is_a_405(world):
    refused = world.request("PATCH", _collection("party"))
    assert refused["status"] == 405
    assert refused["error"]["code"] == "method_not_allowed"
    assert set(refused["error"]["details"]["allowed"]) == {"GET", "POST"}


def test_a_deep_unknown_path_is_a_404_not_a_405(world):
    refused = world.get("/v1/erp/documents/party/CUST-0001/nonsense")
    assert refused["status"] == 404
    assert refused["error"]["code"] == "not_found"


def test_the_envelope_is_the_house_shape(world):
    """Every reply — ok or refused — is the one envelope, with no stack trace in it."""
    for envelope in (
        world.get(_collection("party")),
        world.get("/v1/erp/nope"),
        world.post(_collection("party"), {"doctype": "party"}),
    ):
        assert set(envelope) == {"ok", "status", "requestId", "data", "error"}
        if envelope["ok"]:
            assert envelope["error"] is None
        else:
            assert envelope["data"] is None
            assert set(envelope["error"]) == {"code", "message", "details"}
            assert "Traceback" not in envelope["error"]["message"]


def test_the_list_is_ordered_by_id(world):
    payload = fixtures.documents()["item"][0]
    for suffix in ("B", "A"):
        world.post(_collection("item"), {**payload, "id": f"ITEM-{suffix}"})
    listed = world.get(_collection("item"))["data"]["items"]
    ids = [item["id"] for item in listed]
    assert ids == sorted(ids)


def err_codes() -> set:
    from integrations.erp.api import errors as err

    return set(err.BOUNDARY_CODES) | set(err.MODEL_STATUS)
