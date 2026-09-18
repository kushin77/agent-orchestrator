"""e2e tests — the published SDK's end-to-end call (issue #1229).

These are the assertion half of ``e2e/sdk_control_plane_call``: the shipped
consumer SDK is driven against the **real** control-plane surface
(``cpapi.ControlPlane.handle``), in-process, through the repo's own session
verification and RBAC enforcement. Before this, the SDK's only callers were its
own tests over a ``FakeControlPlane`` transport (register #1201), so nothing
could tell whether the SDK still matched the surface it documents.
"""

from __future__ import annotations

import pytest

from aosdk.auth import TokenSource
from aosdk.controlplane import ControlPlaneClient
from aosdk.errors import ApiError
from aosdk.model import AuditRecord, PolicyBinding

from e2e.sdk_control_plane_call import (
    SdkControlPlaneTransport,
    build_real_control_plane,
    mint_session,
)

#: The permissions the role under test holds; ``agent:create`` lets the call
#: mutate (which appends a real audit record) so the following read has content.
PERMS = ("agent:create", "policy:read", "audit:read")


def _client(wired):
    return ControlPlaneClient(
        SdkControlPlaneTransport(wired["app"]),
        token_source=TokenSource(callback=lambda: wired["token"]),
    )


def test_sdk_real_round_trip_mutation_then_typed_audit_read():
    """Real end-to-end call: SDK -> real router -> real handler -> typed model.

    The mutation goes through the real authorisation guard and appends to the
    real audit store; the read then returns records the SDK parses into its own
    ``AuditRecord``. Both legs travel the SDK's transport seam.
    """
    wired = build_real_control_plane(permissions=PERMS)
    transport = SdkControlPlaneTransport(wired["app"])
    client = ControlPlaneClient(
        transport, token_source=TokenSource(callback=lambda: wired["token"])
    )

    created = client._call(
        "POST", "/v1/agents", body={"agentId": "sdk-agent-1", "profileRef": "coder"}
    )
    assert created["agentId"] == "sdk-agent-1"

    records = client.query_audit()

    # The real surface received both legs, in order.
    assert transport.calls == [("POST", "/v1/agents"), ("GET", "/v1/audit")]
    assert records, "the real audit store must have the mutation's record"
    assert all(isinstance(record, AuditRecord) for record in records)


def test_sdk_surfaces_the_real_permission_refusal():
    """A denial from the real RBAC guard, surfaced as the SDK's typed error."""
    wired = build_real_control_plane(subject_id="u_nobody", permissions=("agent:read",))
    client = _client(wired)

    with pytest.raises(ApiError) as caught:
        client.query_audit()

    # The code is the server's own vocabulary, not a string this test wrote.
    assert caught.value.status == 403
    assert caught.value.code in {"permission_denied", "scope_denied"}


def test_sdk_surfaces_the_real_authentication_refusal():
    """An unverifiable/foreign-token call is refused by the real server, not bypassed."""
    wired = build_real_control_plane(permissions=PERMS)
    foreign = ControlPlaneClient(
        SdkControlPlaneTransport(wired["app"]),
        token_source=TokenSource(callback=lambda: mint_session("globex", "u_x", role="r")),
    )

    with pytest.raises(ApiError) as caught:
        foreign.query_audit()

    assert caught.value.status in {401, 403}


def test_policies_route_payload_parses_into_the_sdk_model():
    """DRIFT LOCK, flipped — issue #1229/#1230 found this, #1235 fixes it.

    ``GET /v1/policies`` serves the guardrail policy store's view
    (``{"items": [{"id", "description", "mode", "controls": <int>}], "count":
    N}``). ``PolicyBinding.from_dict`` used to assume the ``controls`` field was
    always a list of ids (a policy *bundle* shape) and raised ``TypeError`` on
    the server's int count (register #1230's drift lock). #1235 corrects the
    model instead: it now keeps the server's own fields (``description``,
    ``mode``, ``controls_count``) rather than inventing a control-id list the
    server never sent, so the real payload parses.

    This test asserts the fixed contract holds, using the same live route and
    fixture the original lock used, so it cannot silently regress.
    """
    wired = build_real_control_plane(permissions=PERMS)

    envelope = wired["app"].handle("GET", "/v1/policies", token=wired["token"])
    assert envelope["status"] == 200
    rows = envelope["data"]["items"]
    assert rows, "the real route must serve at least one policy row"

    row = rows[0]
    # The server's shape: a count, under a key the SDK does not read as ids.
    assert isinstance(row["controls"], int)
    assert "policyId" not in row

    # ...and the SDK's model now consumes it faithfully, without inventing data.
    binding = PolicyBinding.from_dict(row)
    assert binding.controls == []
    assert binding.controls_count == row["controls"]
    assert binding.description == row.get("description")
    assert binding.mode == row.get("mode")


def test_drift_lock_still_bites_the_old_imagined_bundle_shape():
    """Negative control: the pre-#1235 assumption still raises TypeError.

    The lock isn't just deleted — a payload with the OLD imagined shape (a
    non-list, non-int ``controls``, the wrong field type entirely) must still
    fail loudly, so a regression back to "coerce whatever's there" is caught.
    """
    bad_row = {"id": "p1", "description": "d", "mode": "enforce", "controls": {"not": "a list or int"}}

    with pytest.raises(TypeError):
        PolicyBinding.from_dict(bad_row)


def test_the_call_is_in_process_and_uses_no_http_transport():
    """The seam under test is in-process: no base_url, no socket, no HttpTransport."""
    wired = build_real_control_plane(permissions=PERMS)
    transport = SdkControlPlaneTransport(wired["app"])

    # An HttpTransport carries a base_url; this one cannot, because it calls
    # the real ControlPlane object directly.
    assert not hasattr(transport, "base_url")

    client = ControlPlaneClient(
        transport, token_source=TokenSource(callback=lambda: wired["token"])
    )
    # A route this role holds a permission for: the call must reach the real
    # app object (``GET /v1/tenants/acme`` would be a real 403 — it needs
    # ``org:read``, which this role deliberately does not hold).
    client._call("GET", "/v1/audit")
    assert transport.calls == [("GET", "/v1/audit")]
    assert transport._app is wired["app"]
