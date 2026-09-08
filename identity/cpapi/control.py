"""ControlPlane facade: authN → route → authZ → handler → envelope.

This is the offline-testable HTTP-handler core of the control plane (issue
#38). A deployment mounts a thin transport adapter in front of
:meth:`ControlPlane.handle` — nothing here opens a socket. Every request:

1. **authN** — a bearer session token is verified into a tenant-scoped
   principal (or an internal principal is supplied for system callers);
2. **route** — ``(method, path)`` is matched against the declarative route
   table;
3. **tenant scope gate** — a path ``tenantId`` must be the session tenant (no
   cross-tenant fallback);
4. **authZ** — the route's ``resource:action`` permission is enforced by the
   injected two-gate guard at the tenant's org node;
5. **handler** — the endpoint runs the injected pillar ports, appends audit
   records, publishes outbox events, and (for destructive routes) honours the
   approval gate;
6. **envelope** — the standardized ``{ok,status,requestId,data,error}``
   response is returned.

All mutations append to the audit ledger and publish their domain event to the
outbox in the same request (the outbox pattern — no dual write).

GET query parameters travel in the ``query`` dict; POST bodies travel in
``body``; both are passed to handlers as the single ``data`` mapping so
endpoints never care which transport carried the parameters.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import errors as err
from .access import (
    AuthenticatedPrincipal,
    authorize_request,
    enforce_scope,
    verify_token,
)
from .approvals import ApprovalGate
from .model import (
    ApprovalView,
    DispatchTaskRequest,
    LifecycleRequest,
    RegisterAgentRequest,
    TenantActionRequest,
    View,
    clamp_limit,
    new_request_id,
)
from .outbox import Outbox
from .ports import (
    AgentOps,
    AuditLedger,
    Authorizer,
    BudgetOps,
    Clock,
    PersonaRegistryPort,
    PolicyStore,
    ProfileCatalog,
    PromptLibrary,
    SessionVerifier,
    TenantOps,
)
from .router import Route, Router, error_envelope, not_found_envelope, ok_envelope


def _list_payload(views: List[View]) -> Dict[str, Any]:
    return {"items": [v.to_dict() for v in views], "count": len(views)}


def _view_payload(view: View) -> Dict[str, Any]:
    return view.to_dict()


class ControlPlane:
    """The offline control-plane API facade (see module docstring)."""

    def __init__(
        self,
        *,
        session_verifier: SessionVerifier,
        authorizer: Authorizer,
        agent_ops: AgentOps,
        profiles: ProfileCatalog,
        personas: PersonaRegistryPort,
        prompts: PromptLibrary,
        policies: PolicyStore,
        tenants: TenantOps,
        budgets: BudgetOps,
        audit: AuditLedger,
        outbox: Outbox,
        approvals: ApprovalGate,
        clock: Clock,
        rng: Optional[Any] = None,
    ) -> None:
        self.session_verifier = session_verifier
        self.authorizer = authorizer
        self.agent_ops = agent_ops
        self.profiles = profiles
        self.personas = personas
        self.prompts = prompts
        self.policies = policies
        self.tenants = tenants
        self.budgets = budgets
        self.audit = audit
        self.outbox = outbox
        self.approvals = approvals
        self.clock = clock
        self._rng = rng or (lambda: uuid.uuid4().hex)
        self.router = Router().register_all(self.routes())

    # --- route table -----------------------------------------------------------

    def routes(self) -> List[Route]:
        return [
            # tenant / org + safety rails
            Route("GET", "/v1/tenants/{tenantId}", "org:read", "tenant.get"),
            Route("POST", "/v1/tenants/{tenantId}/pause", "budget:manage", "tenant.pause", destructive=True),
            Route("POST", "/v1/tenants/{tenantId}/resume", "budget:manage", "tenant.resume"),
            Route("GET", "/v1/tenants/{tenantId}/usage", "budget:read", "budget.usage"),
            Route("GET", "/v1/tenants/{tenantId}/quotas", "budget:read", "budget.quotas"),
            # agents + lifecycle + tasks
            Route("GET", "/v1/agents", "agent:read", "agents.list"),
            Route("POST", "/v1/agents", "agent:create", "agents.register"),
            Route("GET", "/v1/agents/{agentId}", "agent:read", "agents.get"),
            Route("POST", "/v1/agents/{agentId}/activate", "agent:write", "agents.activate"),
            Route("POST", "/v1/agents/{agentId}/pause", "agent:write", "agents.pause"),
            Route("POST", "/v1/agents/{agentId}/retire", "agent:delete", "agents.retire", destructive=True),
            Route("POST", "/v1/agents/{agentId}/tasks", "agent:run", "agents.dispatch"),
            Route("GET", "/v1/agents/{agentId}/tasks/{taskId}", "agent:read", "tasks.status"),
            # registry reads (profiles / personas / prompt modules)
            Route("GET", "/v1/profiles", "prompt:read", "profiles.list"),
            Route("GET", "/v1/profiles/{profileId}", "prompt:read", "profiles.get"),
            Route("GET", "/v1/personas", "prompt:read", "personas.list"),
            Route("GET", "/v1/personas/{personaId}", "prompt:read", "personas.get"),
            Route("GET", "/v1/prompts", "prompt:read", "prompts.list"),
            Route("GET", "/v1/prompts/{moduleId}", "prompt:read", "prompts.get"),
            # guardrail policies
            Route("GET", "/v1/policies", "policy:read", "policies.list"),
            Route("GET", "/v1/policies/{policyId}", "policy:read", "policies.get"),
            # audit + outbox + approvals
            Route("GET", "/v1/audit", "audit:read", "audit.query"),
            Route("GET", "/v1/outbox/events", "event:read", "outbox.events"),
            Route("POST", "/v1/outbox/poll", "event:consume", "outbox.poll"),
            Route("POST", "/v1/outbox/{eventId}/ack", "event:consume", "outbox.ack"),
            Route("POST", "/v1/outbox/{eventId}/fail", "event:consume", "outbox.fail"),
            Route("GET", "/v1/approvals", "approval:read", "approvals.list"),
            Route("POST", "/v1/approvals/{approvalId}/approve", "approval:approve", "approvals.approve"),
            Route("POST", "/v1/approvals/{approvalId}/deny", "approval:approve", "approvals.deny"),
        ]

    # --- request entry ----------------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        principal: Optional[AuthenticatedPrincipal] = None,
    ) -> Dict[str, Any]:
        """Handle one HTTP-shaped request and return the envelope.

        ``principal`` is for internal/system/test callers; real traffic passes
        ``token`` which is verified by the injected :class:`SessionVerifier`.
        GET query parameters ride in ``query``; POST bodies in ``body``.
        """
        request_id = new_request_id(self._rng)
        matched = self.router.match(method, path)
        if matched is None:
            return not_found_envelope(request_id, path)
        route = matched.route
        try:
            if principal is None:
                principal = verify_token(
                    self.session_verifier, token, expected_tenant=None
                )
            params = matched.params
            if route.requires_tenant_scope and "tenantId" in params:
                enforce_scope(principal, params["tenantId"])
            authorize_request(self.authorizer, principal, principal.tenant_id, route.permission)
            data: Dict[str, Any] = {}
            if method == "GET":
                data = dict(query or {})
            else:
                # POST: pass the raw body through unchanged so strict parsers
                # can distinguish "no body" (None) from "not an object".
                data = body  # type: ignore[assignment]
            handler = getattr(self, f"_on_{route.name.replace('.', '_')}")
            status, response_data = handler(params, data, principal)
            return ok_envelope(response_data, request_id, status=status)
        except err.ApiError as error:
            return error_envelope(error, request_id)
        except Exception as error:  # noqa: BLE001 - never leak a stack as a 500
            return error_envelope(
                err.ApiError(
                    500,
                    "internal_error",
                    "an internal error occurred while handling the request",
                    {"kind": type(error).__name__},
                ),
                request_id,
            )

    # --- helpers ----------------------------------------------------------------

    def _audit(
        self,
        principal: AuthenticatedPrincipal,
        action: str,
        resource: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit.append(
            tenant_id=principal.tenant_id,
            actor=principal.actor,
            action=action,
            resource=resource,
            detail=detail,
        )

    def _emit(
        self,
        event_type: str,
        tenant_id: str,
        *,
        aggregate_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        actor: str,
        idempotency_key: Optional[str] = None,
    ) -> None:
        self.outbox.publish(
            event_type,
            tenant_id,
            aggregate_id=aggregate_id,
            payload=payload,
            actor=actor,
            idempotency_key=idempotency_key,
        )

    def _approval_required_payload(
        self, principal: AuthenticatedPrincipal, approval: ApprovalView
    ) -> Dict[str, Any]:
        self._emit(
            "approval.required",
            principal.tenant_id,
            aggregate_id=approval.resource,
            payload={
                "action": approval.action,
                "approvalId": approval.approvalId,
                "reason": approval.reason,
            },
            actor=principal.actor,
            idempotency_key=f"approval:{approval.approvalId}",
        )
        self._audit(
            principal,
            "approval.request",
            approval.resource,
            {"approvalId": approval.approvalId},
        )
        return {
            "status": "approval_required",
            "message": "this destructive action awaits approval by an authorized approver",
            "approval": approval.to_dict(),
        }

    # --- tenant / org -------------------------------------------------------------

    def _on_tenant_get(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        tenant = self.tenants.get(principal.tenant_id)
        return 200, _view_payload(tenant)

    def _on_tenant_pause(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        request = TenantActionRequest.parse(data)
        action, resource = "tenant.pause", principal.tenant_id
        approved, approval = self.approvals.guard(
            action=action,
            resource=resource,
            requester=principal.actor,
            reason=request.reason or "",
        )
        if not approved:
            return 202, self._approval_required_payload(principal, approval)
        tenant = self.budgets.pause(
            principal.tenant_id,
            actor=principal.actor,
            reason=request.reason or "control-plane pause",
        )
        self.approvals.store.consume(approval.approvalId)
        self._audit(principal, "tenant.pause", resource, {"reason": request.reason})
        self._emit(
            "control.pause",
            principal.tenant_id,
            aggregate_id=resource,
            actor=principal.actor,
            payload={"reason": request.reason},
            idempotency_key=f"pause:{resource}:{approval.approvalId}",
        )
        return 200, _view_payload(tenant)

    def _on_tenant_resume(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        request = TenantActionRequest.parse(data)
        tenant = self.budgets.resume(principal.tenant_id, actor=principal.actor)
        self._audit(principal, "tenant.resume", principal.tenant_id, {"reason": request.reason})
        self._emit(
            "control.resume",
            principal.tenant_id,
            aggregate_id=principal.tenant_id,
            actor=principal.actor,
            payload={"reason": request.reason},
        )
        return 200, _view_payload(tenant)

    def _on_budget_usage(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _view_payload(self.budgets.usage(principal.tenant_id))

    def _on_budget_quotas(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _view_payload(self.budgets.quotas(principal.tenant_id))

    # --- agents -------------------------------------------------------------------

    def _on_agents_list(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        agents = self.agent_ops.list(principal.tenant_id, status=data.get("status"))
        return 200, _list_payload(agents)

    def _on_agents_register(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        request = RegisterAgentRequest.parse(data)
        agent = self.agent_ops.register(
            principal.tenant_id, request.agent_id, request.profile_ref, actor=principal.actor
        )
        self._audit(
            principal, "registry.register", agent.agentId, {"profileRef": agent.profileRef}
        )
        self._emit(
            "agent.registered",
            principal.tenant_id,
            aggregate_id=agent.agentId,
            actor=principal.actor,
            payload={"profileRef": agent.profileRef},
            idempotency_key=f"register:{principal.tenant_id}:{agent.agentId}",
        )
        self._emit(
            "agent.provision",
            principal.tenant_id,
            aggregate_id=agent.agentId,
            actor=principal.actor,
            payload={"profileRef": agent.profileRef},
            idempotency_key=f"provision:{principal.tenant_id}:{agent.agentId}",
        )
        return 201, _view_payload(agent)

    def _on_agents_get(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        agent = self.agent_ops.get(principal.tenant_id, params["agentId"])
        return 200, _view_payload(agent)

    def _lifecycle(
        self,
        action: str,
        event_type: str,
        params: Dict[str, str],
        data: Dict[str, Any],
        principal: AuthenticatedPrincipal,
    ) -> Tuple[int, Dict[str, Any]]:
        request = LifecycleRequest.parse(data)
        agent_id = params["agentId"]
        if action == "activate":
            agent = self.agent_ops.activate(principal.tenant_id, agent_id, actor=principal.actor)
        else:  # pause
            agent = self.agent_ops.pause(principal.tenant_id, agent_id, actor=principal.actor)
        self._audit(principal, f"registry.{action}", agent_id, {"reason": request.reason})
        self._emit(
            event_type,
            principal.tenant_id,
            aggregate_id=agent_id,
            actor=principal.actor,
            payload={"reason": request.reason},
            idempotency_key=f"{action}:{principal.tenant_id}:{agent_id}",
        )
        return 200, _view_payload(agent)

    def _on_agents_activate(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return self._lifecycle("activate", "agent.activated", params, data, principal)

    def _on_agents_pause(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return self._lifecycle("pause", "agent.paused", params, data, principal)

    def _on_agents_retire(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        request = LifecycleRequest.parse(data)
        action, resource = "agent.retire", params["agentId"]
        approved, approval = self.approvals.guard(
            action=action,
            resource=resource,
            requester=principal.actor,
            reason=request.reason or "",
        )
        if not approved:
            return 202, self._approval_required_payload(principal, approval)
        agent = self.agent_ops.retire(principal.tenant_id, resource, actor=principal.actor)
        self.approvals.store.consume(approval.approvalId)
        self._audit(
            principal, "registry.retire", resource,
            {"reason": request.reason, "approvalId": approval.approvalId},
        )
        self._emit(
            "agent.retired",
            principal.tenant_id,
            aggregate_id=resource,
            actor=principal.actor,
            payload={"reason": request.reason},
            idempotency_key=f"retire:{principal.tenant_id}:{resource}",
        )
        return 200, _view_payload(agent)

    def _on_agents_dispatch(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        request = DispatchTaskRequest.parse(data)
        task = self.agent_ops.dispatch_task(
            principal.tenant_id,
            params["agentId"],
            request.task_type,
            request.input,
            actor=principal.actor,
            idempotency_key=request.idempotency_key,
        )
        self._audit(
            principal, "task.dispatch", params["agentId"],
            {"taskId": task.taskId, "taskType": task.taskType},
        )
        self._emit(
            "task.dispatched",
            principal.tenant_id,
            aggregate_id=task.taskId,
            actor=principal.actor,
            payload={"agentId": params["agentId"], "taskType": task.taskType},
            idempotency_key=request.idempotency_key
            or f"dispatch:{principal.tenant_id}:{task.taskId}",
        )
        return 202, _view_payload(task)

    def _on_tasks_status(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        task = self.agent_ops.task_status(principal.tenant_id, params["taskId"])
        return 200, _view_payload(task)

    # --- registry reads -------------------------------------------------------------

    def _on_profiles_list(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _list_payload(self.profiles.list(principal.tenant_id))

    def _on_profiles_get(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _view_payload(self.profiles.get(principal.tenant_id, params["profileId"]))

    def _on_personas_list(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _list_payload(self.personas.list(principal.tenant_id))

    def _on_personas_get(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _view_payload(self.personas.get(principal.tenant_id, params["personaId"]))

    def _on_prompts_list(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _list_payload(self.prompts.list(principal.tenant_id))

    def _on_prompts_get(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _view_payload(self.prompts.get(principal.tenant_id, params["moduleId"]))

    def _on_policies_list(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _list_payload(self.policies.list(principal.tenant_id))

    def _on_policies_get(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return 200, _view_payload(self.policies.get(principal.tenant_id, params["policyId"]))

    # --- audit ------------------------------------------------------------------------

    def _on_audit_query(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        tenant_id = data.get("tenantId") or principal.tenant_id
        if tenant_id != principal.tenant_id:
            enforce_scope(principal, tenant_id)
        records = self.audit.query(
            tenant_id=tenant_id,
            action=data.get("action"),
            actor=data.get("actor"),
            limit=clamp_limit(data.get("limit")),
        )
        return 200, {"items": [r.to_dict() for r in records], "count": len(records)}

    # --- outbox consumer contract -----------------------------------------------------

    def _on_outbox_events(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        limit = clamp_limit(data.get("limit"))
        events = self.outbox.events_for(principal.tenant_id, limit=limit)
        return 200, _list_payload(events)

    def _on_outbox_poll(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        data = data if isinstance(data, dict) else {}
        consumer = data.get("consumer") or principal.actor
        limit = clamp_limit(data.get("limit"), default=10)
        events = self.outbox.poll(limit=limit, consumer=consumer)
        return 200, _list_payload(events)

    def _on_outbox_ack(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        data = data if isinstance(data, dict) else {}
        consumer = data.get("consumer") or principal.actor
        event = self.outbox.ack(params["eventId"], consumer=consumer)
        return 200, _view_payload(event)

    def _on_outbox_fail(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        data = data if isinstance(data, dict) else {}
        consumer = data.get("consumer") or principal.actor
        error = data.get("error") or "processing failed"
        event = self.outbox.fail(params["eventId"], consumer=consumer, error=str(error))
        return 200, _view_payload(event)

    # --- approvals ----------------------------------------------------------------------

    def _on_approvals_list(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        approvals = self.approvals.store.list(status=data.get("status"))
        return 200, _list_payload(approvals)

    def _decide_approval(
        self,
        approve: bool,
        action_audit: str,
        event_type: str,
        params: Dict[str, str],
        data: Dict[str, Any],
        principal: AuthenticatedPrincipal,
    ) -> Tuple[int, Dict[str, Any]]:
        approval = self.approvals.store.decide(
            params["approvalId"], approve=approve, approver=principal.actor
        )
        self._audit(
            principal, action_audit, approval.resource,
            {"approvalId": approval.approvalId, "action": approval.action},
        )
        self._emit(
            event_type,
            principal.tenant_id,
            aggregate_id=approval.resource,
            actor=principal.actor,
            payload={"approvalId": approval.approvalId, "action": approval.action},
        )
        return 200, _view_payload(approval)

    def _on_approvals_approve(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return self._decide_approval(
            True, "approval.approve", "approval.approved", params, data, principal
        )

    def _on_approvals_deny(
        self, params: Dict[str, str], data: Dict[str, Any], principal: AuthenticatedPrincipal
    ) -> Tuple[int, Dict[str, Any]]:
        return self._decide_approval(
            False, "approval.deny", "approval.denied", params, data, principal
        )
