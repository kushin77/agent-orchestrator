"""Thin typed control-plane client (mirrors ``openapi.yaml``).

Offline companion to the OpenAPI spec (issue #38 AC4): a small typed client
that speaks the exact envelope + routes documented in ``openapi.yaml`` over an
injected :class:`Transport`. In a real deployment the transport is HTTP
(requests/aiohttp or a generated SDK); in tests it is the in-process
:class:`cpapi.transport.InProcessTransport`, so the whole client is verifiable
offline with no network. Because the spec is the contract, a richer client can
also be *generated* from ``openapi.yaml`` by standard code generators at build
time (see ``clients/README.md``) — this module is the hand-written, dependency
free core that exercises the same wire contract.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Protocol

from ..errors import ApiError


class Transport(Protocol):
    """One request/response round trip returning the standard envelope."""

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]: ...


def _require_ok(envelope: Dict[str, Any]) -> Any:
    """Unwrap an envelope's data or raise the embedded ApiError."""
    if not envelope.get("ok"):
        error = envelope.get("error") or {}
        raise ApiError(
            status=envelope.get("status", 500),
            code=error.get("code", "unknown_error"),
            message=error.get("message", "request failed"),
            details=error.get("details"),
        )
    return envelope.get("data")


class ControlPlaneClient:
    """Typed client for the control-plane REST surface (spec 1.0.0)."""

    def __init__(self, transport: Transport, *, token: Optional[str] = None) -> None:
        self._transport = transport
        self._token = token

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return _require_ok(
            self._transport.request(
                method, path, body=body, query=query, token=self._token
            )
        )

    def _list(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(data.get("items", []))

    # --- tenant / budget -----------------------------------------------------

    def get_tenant(self, tenant_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/tenants/{tenant_id}")

    def get_usage(self, tenant_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/tenants/{tenant_id}/usage")

    def get_quotas(self, tenant_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/tenants/{tenant_id}/quotas")

    def pause_tenant(self, tenant_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._call("POST", f"/v1/tenants/{tenant_id}/pause", body={"reason": reason})

    def resume_tenant(self, tenant_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._call("POST", f"/v1/tenants/{tenant_id}/resume", body={"reason": reason})

    # --- agents --------------------------------------------------------------

    def list_agents(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._list(self._call("GET", "/v1/agents", query={"status": status}))

    def register_agent(
        self, agent_id: str, profile_ref: str, role: Optional[str] = None
    ) -> Dict[str, Any]:
        return self._call(
            "POST", "/v1/agents",
            body={"agentId": agent_id, "profileRef": profile_ref, "role": role},
        )

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/agents/{agent_id}")

    def activate_agent(self, agent_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._call("POST", f"/v1/agents/{agent_id}/activate", body={"reason": reason})

    def pause_agent(self, agent_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._call("POST", f"/v1/agents/{agent_id}/pause", body={"reason": reason})

    def retire_agent(self, agent_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return self._call("POST", f"/v1/agents/{agent_id}/retire", body={"reason": reason})

    def dispatch_task(
        self,
        agent_id: str,
        task_type: str,
        input_: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._call(
            "POST", f"/v1/agents/{agent_id}/tasks",
            body={"taskType": task_type, "input": input_ or {}, "idempotencyKey": idempotency_key},
        )

    def get_task_status(self, agent_id: str, task_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/agents/{agent_id}/tasks/{task_id}")

    # --- registry reads -------------------------------------------------------

    def list_profiles(self) -> List[Dict[str, Any]]:
        return self._list(self._call("GET", "/v1/profiles"))

    def get_profile(self, profile_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/profiles/{profile_id}")

    def list_personas(self) -> List[Dict[str, Any]]:
        return self._list(self._call("GET", "/v1/personas"))

    def get_persona(self, persona_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/personas/{persona_id}")

    def list_prompts(self) -> List[Dict[str, Any]]:
        return self._list(self._call("GET", "/v1/prompts"))

    def get_prompt(self, module_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/prompts/{module_id}")

    def list_policies(self) -> List[Dict[str, Any]]:
        return self._list(self._call("GET", "/v1/policies"))

    def get_policy(self, policy_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/v1/policies/{policy_id}")

    # --- governance: audit / outbox / approvals ---------------------------------

    def query_audit(
        self,
        *,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self._list(
            self._call("GET", "/v1/audit", query={"action": action, "actor": actor, "limit": limit})
        )

    def list_outbox_events(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self._list(self._call("GET", "/v1/outbox/events", query={"limit": limit}))

    def poll_outbox(self, consumer: str, limit: int = 10) -> List[Dict[str, Any]]:
        return self._list(
            self._call("POST", "/v1/outbox/poll", body={"consumer": consumer, "limit": limit})
        )

    def ack_outbox(self, event_id: str, consumer: str) -> Dict[str, Any]:
        return self._call("POST", f"/v1/outbox/{event_id}/ack", body={"consumer": consumer})

    def fail_outbox(self, event_id: str, consumer: str, error: str) -> Dict[str, Any]:
        return self._call(
            "POST", f"/v1/outbox/{event_id}/fail", body={"consumer": consumer, "error": error}
        )

    def list_approvals(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._list(self._call("GET", "/v1/approvals", query={"status": status}))

    def approve(self, approval_id: str) -> Dict[str, Any]:
        return self._call("POST", f"/v1/approvals/{approval_id}/approve", body={})

    def deny(self, approval_id: str) -> Dict[str, Any]:
        return self._call("POST", f"/v1/approvals/{approval_id}/deny", body={})
