"""The error model: derived statuses, the closed vocabulary, and pass-through (#651).

The surface does not invent a dialect. Its own boundary refusals are a closed set,
the ERP model's refusals travel through **unchanged** (same code, same status), and
the ERP-08 layer's decision reasons are all *placed* — a reason with no placement
would reach a client as an unexplained 500, so the mapping is checked against the
auth vocabulary rather than trusted. These tests exercise all three, including the
provoked case of a reason the auth lane adds later.
"""

from __future__ import annotations

import pytest

from integrations.erp.api import errors as err
from integrations.erp.auth import model as auth_model
from integrations.erp.auth.model import Decision, Refused
from integrations.erp.core import errors as model_errors


def test_the_boundary_vocabulary_is_closed():
    assert err.BOUNDARY_CODES == tuple(sorted(err.BOUNDARY_CODES)), "declared sorted, so a diff reads"
    assert len(set(err.BOUNDARY_CODES)) == len(err.BOUNDARY_CODES)
    for code in err.BOUNDARY_CODES:
        error = {
            "conflict": err.conflict,
            "document_not_found": err.document_not_found,
            "forbidden": lambda m: err.forbidden(m, reason="permission-denied"),
            "internal": err.internal,
            "method_not_allowed": lambda m: err.method_not_allowed(m, allowed=("GET",)),
            "not_found": err.not_found,
            "unauthorized": err.unauthorized,
            "unavailable": lambda m: err.unavailable(m, reason="contract-unavailable"),
        }[code]("probe")
        assert error.code == code
        assert isinstance(error, model_errors.ApiError), "one envelope builder serves them all"


def test_a_code_outside_the_vocabulary_cannot_be_built():
    """A boundary refusal that invents a code is a bug in the refuser, caught at the raise."""
    with pytest.raises(ValueError):
        err.SurfaceError(400, "invented_code", "probe", None)


def test_the_model_statuses_are_read_from_the_model():
    """Every code the model declares has a status, and it is the model's own."""
    assert tuple(sorted(err.MODEL_STATUS)) == tuple(sorted(model_errors.CODES))
    assert err.MODEL_STATUS["schema_violation"] == model_errors.schema_violation("probe").status
    assert err.MODEL_STATUS["state_jumped"] == model_errors.state_jumped("w", "a", "b").status


def test_every_auth_reason_is_placed():
    """The mapping accounts for the auth lane's whole closed vocabulary."""
    assert set(err.AUTH_STATUS) == set(auth_model.REFUSALS)


def test_the_emitted_refusals_are_the_union_of_both_vocabularies():
    codes = {code for _status, code in err.EMITTED_REFUSALS}
    assert codes == set(err.BOUNDARY_CODES) | set(err.MODEL_STATUS)
    assert err.STATUSES == tuple(sorted({status for status, _code in err.EMITTED_REFUSALS}))
    assert 200 not in err.STATUSES and 201 not in err.STATUSES


@pytest.mark.parametrize("reason", sorted(auth_model.REFUSALS))
def test_every_auth_reason_maps_to_a_declared_status(reason):
    status, code = err.AUTH_STATUS[reason]
    assert status in err.STATUSES
    assert code in err.BOUNDARY_CODES
    # And the mapping's status is the one the refusal object actually carries.
    refusal = err.from_decision(Decision(allowed=False, reason=reason, detail="probe"))
    assert refusal.status == status, reason
    assert refusal.code == code, reason
    assert refusal.details["reason"] == reason, "the auth layer's why is carried, not rewritten"


def test_a_cross_tenant_denial_is_not_an_existence_oracle():
    refusal = err.from_decision(
        Decision(allowed=False, reason="cross-tenant", detail="probe"), kind="sales-order"
    )
    assert refusal.status == 404
    assert refusal.code == "document_not_found"
    assert "sales-order" in refusal.message


def test_a_raised_refusal_is_cannot_assess():
    """The auth layer raises only when its declarations cannot be read."""
    refusal = err.from_refusal(Refused("contract-unavailable", "provoked"))
    assert refusal.status == 503
    assert refusal.code == "unavailable"
    assert refusal.details["reason"] == "contract-unavailable"


def test_the_model_refusals_arrive_unchanged(world):
    """The pass-through property, measured: same code, same status, same details."""
    refused = world.post("/v1/erp/documents/party", {"doctype": "party", "id": "CUST-1"})
    assert refused["error"]["code"] == "schema_violation"
    assert refused["status"] == err.MODEL_STATUS["schema_violation"]
    assert refused["error"]["details"]["problems"]

    jumped = world.post("/v1/erp/documents/sales-order/SALES-ORDER-0001/transitions/complete")
    assert jumped["error"]["code"] == "unknown_action"
    assert jumped["status"] == err.MODEL_STATUS["unknown_action"]


def test_a_non_mapping_body_is_the_models_invalid_body(world):
    refused = world.post("/v1/erp/documents/party", ["not", "an", "object"])
    assert refused["status"] == 400
    assert refused["error"]["code"] == "invalid_body"


def test_an_unexpected_failure_never_leaks_a_stack(world):
    """A 500 is an envelope, not a traceback — and it names only the exception type (GR-6)."""
    class Boom:
        def schema_for(self, kind):
            raise RuntimeError("a secret value that must not travel")

    broken = world.surface
    broken.model = Boom()
    refused = world.get("/v1/erp/documents/party")
    assert refused["status"] == 500
    assert refused["error"]["code"] == "internal"
    assert refused["error"]["details"]["exception"] == "RuntimeError"
    assert "secret value" not in refused["error"]["message"]
    assert "Traceback" not in refused["error"]["message"]
