"""e2e/sdk_control_plane_call — the published SDK's end-to-end call (issue #1229).

WHY this exists. The consumer SDK (`control-plane/sdk/python/aosdk`) had **no
in-repo consumer**: its only callers were its own tests, which drive a
``FakeControlPlane`` transport, so ``test_end_to_end.py`` was self-referential
and the SDK could drift from the surface it documents with nothing to catch it
(deep-review register #1201). ``e2e/golden_path.py`` exercises the real pillar
modules, but through ``e2e/wiring.py``'s bundle — never the shipped SDK.

WHAT this does. It binds the SDK's own ``transport.Transport`` seam onto the
**real** control-plane surface, ``cpapi.ControlPlane.handle``, and then makes a
real call through the whole stack:

    aosdk.ControlPlaneClient
      -> SdkControlPlaneTransport          (this module: the only new seam)
      -> cpapi.ControlPlane.handle          (real router + route table)
      -> identity/cpapi access.verify_token (real HS256 session verification)
      -> enforce_scope + authorize_request  (real identity/rbac guard)
      -> the route handler
      -> ok_envelope / error_envelope       (the real envelope + error vocab)
      -> aosdk require_ok -> typed model, or the typed ApiError

Nothing is faked on the wire path. The session verifier is the repo's own
``cpapi.wiring.SsoSessionVerifier``; the only local object is a small adapter
that gives it the claims, by calling the repo's own
``identity.sso.tokens.verify_session_token`` (signature + expiry + purpose +
tenant claim, fail closed). No network, no containers, no server socket: the
call runs in-process against ``ControlPlane.handle``.

Run it:

    python3 -m e2e.sdk_control_plane_call [--out DIR]
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

#: The shipped SDK lives outside the pillar roots the e2e suite already adds.
SDK_ROOT = os.path.join(REPO_ROOT, "control-plane", "sdk", "python")
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

TENANT = "acme"

#: A fixed key for the offline run. It never leaves this process and is not a
#: secret: the token it signs is minted and consumed in the same call.
_HMAC_KEY = b"e2e-sdk-call-offline-key-not-a-secret"


class SdkControlPlaneTransport:
    """The SDK's ``Transport`` seam bound onto a real ``ControlPlane``.

    This is the only object in the call that is not the repo's own server or
    the repo's own SDK, and it adds no behaviour: it forwards the SDK's
    ``(method, path, body, query, token)`` to the real request entry point and
    returns the envelope unchanged. ``headers`` is accepted because the SDK's
    protocol declares it; an in-process call has no transport headers, and the
    bearer token travels in ``token`` exactly as ``handle`` expects.
    """

    def __init__(self, control_plane: Any) -> None:
        self._app = control_plane
        self.calls: List[Tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        self.calls.append((method, path))
        return self._app.handle(method, path, body=body, query=query, token=token)


class _SsoTokenClaims:
    """Give ``SsoSessionVerifier`` the claims, verified by the repo's own code.

    ``cpapi.wiring.SsoSessionVerifier`` expects an ``identity/sso`` service
    exposing ``verify_session(token, expected_tenant=...)``. Rather than build a
    whole ``SsoService`` (which also needs a keystore and a tenant SSO store),
    this adapter answers that one call by delegating to
    ``identity.sso.tokens.verify_session_token`` — the same function the service
    uses — so signature, expiry, purpose and the mandatory tenant claim are all
    checked by the repo's fail-closed verifier, not by a re-implementation.
    """

    def verify_session(
        self, token: str, expected_tenant: Optional[str] = None
    ) -> Dict[str, Any]:
        from sso.tokens import session_matches_tenant, verify_session_token

        claims = verify_session_token(token, _HMAC_KEY, now=int(time.time()))
        if expected_tenant is not None and not session_matches_tenant(
            claims, expected_tenant
        ):
            from cpapi import errors as cpapi_errors

            raise cpapi_errors.cross_tenant()
        return claims


def mint_session(tenant_id: str, subject_id: str, *, role: str, ttl: int = 600) -> str:
    """Mint a real HS256 session token with the repo's own issuer."""
    from sso.tokens import issue_session_token

    token, _claims = issue_session_token(
        _HMAC_KEY,
        tenant_id=tenant_id,
        subject_id=subject_id,
        subject_type="user",
        role=role,
        now=int(time.time()),
        ttl=ttl,
    )
    return token


def build_real_control_plane(
    *,
    tenant_id: str = TENANT,
    subject_id: str = "u_sdk",
    role_name: str = "sdk-consumer",
    permissions: Tuple[str, ...] = ("policy:read", "audit:read"),
) -> Dict[str, Any]:
    """Wire a control plane whose authN/authZ are the repo's real engines.

    Only the *seams with no offline implementation* stay doubles (the budget /
    tenant / registry ports); the router, the route table, the envelope, the
    error vocabulary, the RBAC guard and the session verification are real.
    """
    import rbac

    from cpapi.fakes import build_test_app
    from cpapi.wiring import RbacAuthorizer, SsoSessionVerifier

    store = rbac.InMemoryStore()
    store.add_org(tenant_id, tenant_id.capitalize(), tenant_type="platform")
    role = store.create_role(
        tenant_id, role_name, "SDK consumer", permissions=permissions, level="org"
    )
    store.add_binding(tenant_id, subject_id, "user", role.id)

    rig = build_test_app(tenant_id=tenant_id, admin_subject="u_admin")

    # Swap in the real engines. ``build_test_app`` builds its own
    # ``session_verifier`` / ``authorizer`` and passes them to ``ControlPlane``
    # by name, so ``**overrides`` cannot replace those two seams (it raises
    # "got multiple values for keyword argument"); assigning them on the app is
    # the pattern the repo's own wiring test uses (`identity/cpapi/tests/
    # test_wiring_rbac.py`). ``Router`` is built from the class's own route
    # table at construction, so swapping either seam keeps the real router.
    rig.app.authorizer = RbacAuthorizer(store)
    rig.app.session_verifier = SsoSessionVerifier(_SsoTokenClaims())

    return {
        "app": rig.app,
        "store": store,
        "role_id": role.id,
        "subject_id": subject_id,
        "tenant_id": tenant_id,
        "token": mint_session(tenant_id, subject_id, role=role_name),
    }


def run() -> Dict[str, Any]:
    """Make the end-to-end calls and return the evidence document."""
    from aosdk.auth import TokenSource
    from aosdk.controlplane import ControlPlaneClient
    from aosdk.errors import ApiError

    wired = build_real_control_plane()
    transport = SdkControlPlaneTransport(wired["app"])
    client = ControlPlaneClient(
        transport, token_source=TokenSource(callback=lambda: wired["token"])
    )

    evidence: Dict[str, Any] = {
        "surface": "cpapi.ControlPlane.handle",
        "sdk": "aosdk.ControlPlaneClient",
        "in_process": True,
        "stages": [],
    }

    # 1. a real authorised read the SDK models correctly: the typed model is
    #    built from the real envelope, produced by the real router.
    audit = client.query_audit()
    evidence["stages"].append(
        {"call": "query_audit", "typed": "AuditRecord[]", "count": len(audit)}
    )

    # 2. DRIFT (found by this call, issue #1229): the real ``GET /v1/policies``
    #    payload is the guardrail policy store's view — ``{"items": [{"id",
    #    "description", "mode", "controls": <int>}], "count": N}`` — while the
    #    SDK's ``PolicyBinding`` models a policy *bundle* (``policyId``,
    #    ``bundle``, ``controls: [ids]``). The SDK cannot parse the real route.
    #    Recorded, not hidden: whichever side is corrected, this entry and the
    #    drift-lock test must be updated together.
    try:
        policies = client.list_policies()
        evidence["stages"].append(
            {"call": "list_policies", "outcome": "parsed", "count": len(policies)}
        )
    except Exception as exc:  # noqa: BLE001 - the drift is the finding
        evidence["stages"].append(
            {
                "call": "list_policies",
                "outcome": "DRIFT",
                "error": f"{type(exc).__name__}: {exc}",
                "server_shape": "{'id','description','mode','controls': <int>}",
                "sdk_model": "aosdk.model.PolicyBinding ('policyId', 'controls': [ids])",
            }
        )

    # 2. a real refusal, produced by the real guard and surfaced typed.
    denied = build_real_control_plane(subject_id="u_nobody", permissions=("agent:read",))
    denied_client = ControlPlaneClient(
        SdkControlPlaneTransport(denied["app"]),
        token_source=TokenSource(callback=lambda: denied["token"]),
    )
    try:
        denied_client.list_policies()
        evidence["stages"].append({"call": "list_policies(denied)", "outcome": "ALLOWED"})
    except ApiError as exc:
        evidence["stages"].append(
            {
                "call": "list_policies(denied)",
                "outcome": "refused",
                "status": exc.status,
                "code": exc.code,
            }
        )

    # 3. a real authN refusal: an unverifiable token is refused by the verifier.
    forged = ControlPlaneClient(
        SdkControlPlaneTransport(wired["app"]),
        token_source=TokenSource(callback=lambda: mint_session("other", "u_x", role="r")),
    )
    try:
        forged.query_audit()
        evidence["stages"].append({"call": "query_audit(bad token)", "outcome": "ALLOWED"})
    except ApiError as exc:
        evidence["stages"].append(
            {
                "call": "query_audit(bad token)",
                "outcome": "refused",
                "status": exc.status,
                "code": exc.code,
            }
        )

    evidence["transport_calls"] = transport.calls
    return evidence


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out_dir = None
    if "--out" in args:
        out_dir = args[args.index("--out") + 1]
    evidence = run()
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "sdk-control-plane-call.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"sdk-control-plane-call: evidence -> {path}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
