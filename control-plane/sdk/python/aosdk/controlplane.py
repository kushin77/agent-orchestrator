"""Control-plane client — usage, audit export, policy (issue #41).

Typed client over the control-plane REST surface (issue #38) as published
through the public edge routes (issue #37).  Methods:

- :meth:`get_usage` / :meth:`my_usage` — tenant usage summary
  (``GET /v1/tenants/{tenantId}/usage`` and the public ``me`` route);
- :meth:`export_audit` / :meth:`query_audit` — the audit export feed
  (``GET /v1/tenants/me/audit/export`` / ``GET /v1/audit``);
- :meth:`list_policies` / :meth:`get_policy` — declared policy bindings
  (``GET /v1/policies`` / ``GET /v1/policies/{policyId}``).

Every response rides the standardized control-plane envelope
``{ok, status, requestId, data, error}``; this client unwraps it and raises
the matching :class:`~aosdk.errors.ApiError` on a non-OK envelope.  Requests
carry a short-lived per-tenant session token (env or callback) — never a
hardcoded key.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .auth import SessionToken, TokenSource, verify_not_expired
from .envelope import items, require_ok
from .errors import ConfigurationError
from .model import AuditRecord, PolicyBinding, UsageReport


class ControlPlaneClient:
    """Typed client for the control-plane usage / audit / policy surface."""

    def __init__(
        self,
        transport: Any,
        *,
        token_source: Optional[TokenSource] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        self._transport = transport
        self._token_source = token_source or TokenSource()
        self._tenant_id = tenant_id

    # -- auth / tenant resolution ------------------------------------------- #
    def _token(self) -> str:
        token = self._token_source.require()
        verify_not_expired(token)  # fail closed on a lapsed token
        return token

    def _tenant(self) -> str:
        token = self._token()
        session = SessionToken.parse(token)
        tenant_id = self._tenant_id or session.tenant_id
        if not tenant_id:
            raise ConfigurationError(
                "no tenant context: pass tenant_id or use a session token with a tenantId claim"
            )
        return tenant_id

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Any:
        token = self._token()
        return require_ok(
            self._transport.request(method, path, body=body, query=query, token=token)
        )

    # -- usage ---------------------------------------------------------------- #
    def get_usage(self, tenant_id: Optional[str] = None) -> UsageReport:
        """Tenant usage summary for ``tenant_id`` (or the session tenant).

        ``GET /v1/tenants/{tenantId}/usage`` — the tenant-scoped control-plane
        route (issue #38).
        """
        resolved = self._tenant()
        if tenant_id and tenant_id != resolved:
            # A caller may only read its own tenant (no cross-tenant fallback).
            raise ConfigurationError(
                f"tenant_id {tenant_id!r} differs from the session tenant {resolved!r}"
            )
        data = require_ok(
            self._transport.request(
                "GET", f"/v1/tenants/{resolved}/usage", token=self._token()
            )
        )
        return UsageReport.from_dict(data)

    def my_usage(self) -> UsageReport:
        """Usage for the caller's tenant via the public ``me`` route.

        ``GET /v1/tenants/me/usage`` — the path never names a real tenant
        (issue #37); the edge fills ``tenantId`` from the verified session.
        """
        data = require_ok(
            self._transport.request("GET", "/v1/tenants/me/usage", token=self._token())
        )
        return UsageReport.from_dict(data)

    # -- audit ---------------------------------------------------------------- #
    def export_audit(
        self, *, limit: Optional[int] = None, action: Optional[str] = None
    ) -> List[AuditRecord]:
        """The tenant's audit export feed (public edge route, issue #37).

        ``GET /v1/tenants/me/audit/export`` — append-only audit records the
        tenant may consume for its own compliance trail.
        """
        query: Dict[str, Any] = {}
        if limit is not None:
            query["limit"] = limit
        if action:
            query["action"] = action
        data = require_ok(
            self._transport.request(
                "GET", "/v1/tenants/me/audit/export", query=query, token=self._token()
            )
        )
        return [AuditRecord.from_dict(item) for item in items(data)]

    def query_audit(
        self,
        *,
        limit: Optional[int] = None,
        action: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> List[AuditRecord]:
        """Query the control-plane audit ledger (``GET /v1/audit``, issue #38)."""
        query: Dict[str, Any] = {}
        if limit is not None:
            query["limit"] = limit
        if action:
            query["action"] = action
        if actor:
            query["actor"] = actor
        data = require_ok(
            self._transport.request("GET", "/v1/audit", query=query, token=self._token())
        )
        return [AuditRecord.from_dict(item) for item in items(data)]

    # -- policy --------------------------------------------------------------- #
    def list_policies(self) -> List[PolicyBinding]:
        """Declared policy bindings (``GET /v1/policies``, issue #38)."""
        data = self._call("GET", "/v1/policies")
        return [PolicyBinding.from_dict(item) for item in items(data)]

    def get_policy(self, policy_id: str) -> PolicyBinding:
        """One declared policy binding (``GET /v1/policies/{policyId}``)."""
        data = self._call("GET", f"/v1/policies/{policy_id}")
        return PolicyBinding.from_dict(data)
