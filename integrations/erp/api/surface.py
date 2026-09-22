"""The transport-free REST surface over the indexer-fed document model (#651).

``Surface.handle(method, path, principal=..., body=...)`` takes an HTTP-shaped
request and returns the house envelope. Nothing here opens a port: the surface is
the *contract*, and a deployment mounts a thin adapter in front of the same
``handle`` (the ``identity/cpapi/control.py`` precedent). That is what makes the
whole thing testable offline in the gate.

**The order of the checks is the design, and every step is load-bearing.**

1. **the route.** No match, and no other method renders that path → ``404``; the
   path renders for a different method → ``405`` naming the methods it does take.
2. **the kind.** A kind-scoped route's ``{kind}`` is resolved against ERP-02
   *before* anything about the caller is considered. A kind is part of the public
   contract, so saying "there is no such kind" answers a question about the
   surface, not about a tenant's data. It also keeps an unknown kind from
   reaching the ERP-08 layer, which would have refused it as an ``unknown-kind``
   *permission* decision (403) — a wrong answer to the right question.
3. **the caller.** No principal → ``401``. There is no other way to be
   unauthenticated here, because the surface has no credential of its own to
   check (GR-6): the adapter that mounts it supplies the
   :class:`~integrations.erp.auth.model.Principal`, which carries **references**,
   never a token.
4. **the authorization.** :meth:`Surface.authorize_request` — one method, called
   from exactly one place in this module — delegates to
   ``integrations/erp/auth.scope.authorize``. The ``tenant`` on the request is
   *the principal's own*, always: this surface reads no tenant from the path, the
   query or the body, so there is no way to ask it for another tenant's data. A
   denied decision becomes this surface's ``403`` (or ``404`` for a cross-tenant
   request, which must not become an existence oracle); a ``Refused`` the auth
   layer *raised* means its declarations could not be read, which is
   CANNOT-ASSESS (``503``) and never a decision.
5. **the body.** Only now is the payload read. A caller who may not act never
   receives a verdict about the document they may not act on, which is the
   difference between authorization and a validation oracle.
6. **the model.** The store validates every write through ERP-02, so the model's
   refusals (``schema_violation``, ``state_jumped``, ``unbalanced_posting``, ...)
   reach the client **unchanged** — same code, same status. ``negative_control``
   measures that pass-through, because "one contract, not two dialects" is a
   claim that has to be demonstrated.

**A read projects by omission.** For a read, the decision's ``projection`` is what
is returned and ``redacted`` names what was withheld — a field a policy denies is
*absent*, never blanked, because a blank field still discloses that it exists and
carries a value. The field policy is a function of ``(kind, field, roles)`` and not
of the document, so for a collection one decision yields the visible field set and
each item is projected to it; that is using the auth layer's own answer, not
re-implementing its rule.

---knowledge---
module_id: integrations.erp.api.surface
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Call, Surface]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from identity.cpapi.errors import ApiError
from identity.cpapi.router import error_envelope, ok_envelope

from integrations.erp.auth import scope as auth_scope
from integrations.erp.auth.model import Decision, Principal, Refused, Request
from integrations.erp.core.errors import invalid_body

from . import errors as err
from . import health as health_module
from . import routes

__all__ = ["Call", "Surface"]

#: ``<repo>`` — from ``<repo>/integrations/erp/api/surface.py``.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The path parameters a kind-scoped route always declares.
KIND = routes.KIND_PARAM
DOCUMENT_ID = routes.DOCUMENT_ID_PARAM
ACTION = routes.ACTION_PARAM

#: The platform scope team the ERP module's data lives under.
#:
#: ``identity/rbac``'s scope node is ``(org, team, agent)`` and refuses an
#: agent-level node that names no team, so a decision needs a team. The ERP module
#: has no notion of one: its documents belong to a *tenant*, and the tenant comes
#: from the principal, always. So the team is a declared constant of the surface
#: rather than a value read from a request — there is no parameter, header or path
#: segment that could set it, which is what keeps the node's org and team both
#: beyond a caller's reach.
SCOPE_TEAM = "erp"

#: The decision a route that carries no tenant data is run with. The two platform
#: routes (``openapi.json`` and ``health``) are the *contract* and the
#: *declarations' reachability* — neither is tenant data, so neither is
#: authorized, and both are declared with an empty ``security`` in the document.
#: Making them ask the ERP-08 layer for a permission would be a decision about
#: nothing, and would deny the contract to a caller who is entitled to read it.
_OPEN_DECISION = Decision(allowed=True, reason="not-tenant-scoped")


@dataclass(frozen=True)
class Call:
    """One dispatched request: the route's inputs and the decision it earned.

    ``body`` is the request's *document* body when the route carries one (a create
    or a replace) and ``None`` when it does not (a read, a delete, a transition) —
    so a handler never has to re-decide what kind of body its route can have.

    ``stored`` is the document the surface *peeked* at before deciding, when the
    route addresses one. It is peeked — never *refused* — so that existence is
    reported only after permission, which is what stops the surface answering
    "that id exists" to a caller who may not read it.
    """

    route: routes.Route
    params: Mapping[str, str]
    body: Optional[Mapping[str, Any]]
    principal: Principal
    decision: Decision
    stored: Optional[Mapping[str, Any]] = None


def _as_body(body: Any) -> Dict[str, Any]:
    """The request body as a mapping, refused when it is absent or is not one.

    ``None`` is the sentinel an adapter passes for *no body*, and it is a distinct
    refusal from a body that is not an object: both are the model's own
    ``invalid_body`` (400), because a mutation this surface offers always
    describes a document and neither ``absent`` nor ``42`` is one. (``{}`` is a
    body — an empty document — and fails the *model's* validation instead, which
    is the right refusal from the right layer.)
    """
    if body is None:
        raise invalid_body("a mutation must carry a document body; none was supplied")
    if not isinstance(body, Mapping):
        raise invalid_body(
            f"a document body must be an object, got {type(body).__name__}",
            bodyType=type(body).__name__,
        )
    return dict(body)


class Surface:
    """The ERP REST surface: routes, authorization delegation and the model behind it."""

    def __init__(
        self,
        *,
        model: Any,
        documents: Any,
        role_map: Any,
        policy_set: Any,
        rbac_store: Any,
        root: Optional[Path] = None,
        team: str = SCOPE_TEAM,
        request_ids: Optional[Callable[[], str]] = None,
    ) -> None:
        self.model = model
        self.documents = documents
        self.role_map = role_map
        self.policy_set = policy_set
        self.rbac_store = rbac_store
        self.root = Path(root) if root is not None else REPO_ROOT
        self.team = team
        self._request_ids = request_ids or (lambda: uuid.uuid4().hex)

    # --- the one authorization call site ----------------------------------

    def authorize_request(
        self,
        principal: Principal,
        *,
        kind: str,
        action: str,
        document_id: Optional[str] = None,
        fields: Optional[Mapping[str, Any]] = None,
    ) -> Decision:
        """Ask the ERP-08 layer to decide. **The only authorization call site.**

        The request's ``tenant`` is ``principal.tenant`` — the authority — never a
        value read from the path, the query or the body. There is no parameter
        here (and none on :meth:`handle`) that could name a different tenant, and
        ``scripts/check-erp-api.sh`` measures that this method is the module's only
        call into the auth layer: a second path to a decision would be the back
        door the acceptance criteria forbid.

        ``team`` is this surface's declared scope team (see :data:`SCOPE_TEAM`),
        never a value from the request; the node's org comes from the principal and
        its team from here, so a client can influence neither.

        A ``Refused`` is re-raised as this surface's ``503``: the auth layer raises
        only when its declarations cannot be read, which is CANNOT-ASSESS — never a
        denial, never a pass.
        """
        request = Request(
            tenant=principal.tenant,
            kind=kind,
            action=action,
            document_id=document_id,
            team=self.team,
            fields=dict(fields or {}),
        )
        try:
            return auth_scope.authorize(
                self.role_map, self.policy_set, self.rbac_store, principal, request
            )
        except Refused as refusal:
            raise err.from_refusal(refusal, kind=kind, document_id=document_id or "") from refusal

    # --- the entry point ---------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        *,
        principal: Optional[Principal] = None,
        body: Any = None,
    ) -> Dict[str, Any]:
        """Handle one HTTP-shaped request and return the house envelope."""
        request_id = self._request_ids()
        try:
            payload = self._dispatch(method, path, principal=principal, body=body)
            status, data = payload
            return ok_envelope(data, request_id, status=status)
        except ApiError as refusal:
            return error_envelope(refusal, request_id)
        except Exception as unexpected:  # noqa: BLE001 - never leak a stack as a body
            return error_envelope(
                err.internal(
                    "the surface failed while handling the request",
                    exception=type(unexpected).__name__,
                ),
                request_id,
            )

    def _dispatch(
        self,
        method: str,
        path: str,
        *,
        principal: Optional[Principal],
        body: Any,
    ) -> Tuple[int, Any]:
        matched = routes.match(method, path)
        if matched is None:
            allowed = routes.allowed_methods(path)
            if allowed:
                raise err.method_not_allowed(
                    f"{str(method).upper()} is not allowed on this path",
                    allowed=allowed,
                    path=path,
                )
            raise err.not_found(f"no route for {str(method).upper()} {path}", path=path)

        route = matched.route
        params = dict(matched.params)

        # 3. the caller.
        if principal is None:
            raise err.unauthorized(
                "the request carries no principal; the adapter that mounts this "
                "surface supplies one and it is never a credential (GR-6)",
                route=route.operation_id,
            )

        # The platform routes describe the contract and the declarations' health.
        # Neither carries tenant data, so neither is authorized — and skipping the
        # decision here is a *declared* property of the surface, not an omission:
        # the document marks both operations with an empty security list.
        if not route.kind_scoped:
            handler = self._handler(route)
            return handler(
                self,
                Call(
                    route=route,
                    params=params,
                    body=None,
                    principal=principal,
                    decision=_OPEN_DECISION,
                ),
            )

        # 2. the kind, against the model — a fact about the contract, not a tenant.
        kind = params.get(KIND, "")
        self.model.schema_for(kind)  # the model's own unknown_document_kind (404)

        document_id = params.get(DOCUMENT_ID)

        # 4. what the decision is asked about, and with which fields. A read of
        #    one document needs the document's own fields for the projection, so
        #    it is *peeked* (never refused) before the decision — and the decision
        #    is what decides whether the caller ever learns it was there.
        stored: Optional[Mapping[str, Any]] = None
        document_body: Optional[Dict[str, Any]] = None
        if route.body_required:
            document_body = _as_body(body)
            fields: Mapping[str, Any] = document_body
        elif document_id is not None and not route.mutation:
            stored = self.documents.peek(principal.tenant, kind, document_id)
            fields = stored or {}
        elif route.mutation:
            # A transition and a delete carry no document: there are no fields to
            # project and none to check a write policy against.
            fields = {}
        else:
            fields = self._collection_fields(principal, kind)

        action = route.action
        if action == routes.ACTION_FROM_PATH:
            named = params.get(ACTION, "")
            action = routes.permission_for(named)
            if not action:
                # Fail closed: a workflow move this module cannot place in the ERP
                # permission vocabulary is a declaration defect, and granting it
                # would mean the auth layer refusing it for every principal — a
                # route nobody could use. `check` refuses this before it ships.
                raise err.unavailable(
                    f"the workflow move {named!r} has no ERP permission declared for it",
                    reason="unplaced-workflow-action",
                    kind=kind,
                    action=named,
                )

        decision = self.authorize_request(
            principal,
            kind=kind,
            action=action,
            document_id=document_id,
            fields=fields,
        )
        if not decision.allowed:
            raise err.from_decision(decision, kind=kind, document_id=document_id or "")

        handler = self._handler(route)
        call = Call(
            route=route,
            params=params,
            body=document_body,
            principal=principal,
            decision=decision,
            stored=stored,
        )
        return handler(self, call)

    def _handler(self, route: routes.Route) -> Callable[["Surface", Call], Tuple[int, Any]]:
        """The handler for a route, refusing by name when none is bound."""
        handler = _HANDLERS.get(route.name)
        if handler is None:  # pragma: no cover - the table and the handlers are checked
            raise err.internal(f"no handler is bound to {route.name!r}", operation=route.operation_id)
        return handler

    # --- request shaping ---------------------------------------------------

    def _collection_fields(self, principal: Principal, kind: str) -> Dict[str, Any]:
        """The field-name mask one decision is asked to project for a collection.

        The field policy is a function of ``(kind, field, roles)`` — not of the
        document — so every document of a kind has the same visible field set and
        one decision covers the collection. Only the *names* matter: the decision's
        ``projection`` supplies the visible set, and each item is projected to it.
        """
        names: set[str] = set()
        for document in self.documents.list(principal.tenant, kind):
            names.update(document.keys())
        return {name: None for name in sorted(names)}

    # --- handlers ----------------------------------------------------------

    def _on_list(self, call: Call) -> Tuple[int, Any]:
        kind = call.params[KIND]
        visible = tuple(call.decision.projection)
        items = [
            {name: value for name, value in document.items() if name in visible}
            for document in self.documents.list(call.principal.tenant, kind)
        ]
        return 200, {
            "kind": kind,
            "count": len(items),
            "items": items,
            "redacted": list(call.decision.redacted),
        }

    def _on_get(self, call: Call) -> Tuple[int, Any]:
        kind = call.params[KIND]
        document_id = call.params[DOCUMENT_ID]
        if call.stored is None:
            # The decision was made without it: a caller who may read learns it is
            # absent, a caller who may not never got this far.
            raise err.document_not_found(
                f"no {kind!r} document {document_id!r} in this tenant", kind=kind, id=document_id
            )
        return 200, {
            "document": dict(call.decision.projection),
            "redacted": list(call.decision.redacted),
        }

    def _on_create(self, call: Call) -> Tuple[int, Any]:
        kind = call.params[KIND]
        created = self.documents.create(call.principal.tenant, kind, call.body)
        visible = set(call.decision.projection)
        return 201, {
            "document": {name: value for name, value in created.items() if name in visible},
            "redacted": list(call.decision.redacted),
        }

    def _on_replace(self, call: Call) -> Tuple[int, Any]:
        kind = call.params[KIND]
        document_id = call.params[DOCUMENT_ID]
        replaced = self.documents.replace(call.principal.tenant, kind, document_id, call.body)
        visible = set(call.decision.projection)
        return 200, {
            "document": {name: value for name, value in replaced.items() if name in visible},
            "redacted": list(call.decision.redacted),
        }

    def _on_delete(self, call: Call) -> Tuple[int, Any]:
        kind = call.params[KIND]
        document_id = call.params[DOCUMENT_ID]
        removed = self.documents.delete(call.principal.tenant, kind, document_id)
        return 200, {"deleted": {"kind": kind, "id": removed["id"]}}

    def _on_advance(self, call: Call) -> Tuple[int, Any]:
        """Move a document and report **the move**, not the document's fields.

        A transition carries no caller-supplied fields, so there is nothing for the
        field *write* policy to judge and nothing for the read projection to filter:
        handing the stored document to the decision as if it were a payload would
        make a write-denied field elsewhere in the document refuse a legitimate
        move. So the reply is the document's identity and the state it reached, and
        a caller that wants the fields reads the document — through the read route,
        where the projection is applied. The shape is declared as
        ``TransitionResponse`` in the document, so this is a contract rather than an
        omission.
        """
        kind = call.params[KIND]
        document_id = call.params[DOCUMENT_ID]
        action = call.params[ACTION]
        moved = self.documents.advance(call.principal.tenant, kind, document_id, action)
        return 200, {
            "transition": {
                name: moved[name]
                for name in ("doctype", "id", "state", "docstatus")
                if name in moved
            },
            "action": action,
        }

    def _on_openapi(self, call: Call) -> Tuple[int, Any]:
        from . import openapi as openapi_module

        return 200, openapi_module.build_document(self.root)

    def _on_health(self, call: Call) -> Tuple[int, Any]:
        report = health_module.health(self.root, model=self.model, role_map=self.role_map)
        if report.status == health_module.STATUS_UNHEALTHY:
            raise err.unavailable(
                "a dependency this surface names is unreachable",
                reason="health-unhealthy",
                **report.to_dict(),
            )
        return 200, report.to_dict()


def _handlers_check() -> Tuple[str, ...]:
    """Every way the handler map and the route table disagree; empty means they agree."""
    bound = set(_HANDLERS)
    named = {route.name for route in routes.TABLE.entries}
    return tuple(
        [f"the route {name!r} has no handler" for name in sorted(named - bound)]
        + [f"the handler {name!r} serves no route" for name in sorted(bound - named)]
    )


#: route handler name -> the method that serves it. The route table and this map
#: are checked against each other (:func:`_handlers_check`, driven by
#: ``cli.check`` and by the OpenAPI validator), so a route with no handler and a
#: handler with no route are both refused rather than discovered at runtime.
_HANDLERS: Dict[str, Callable[["Surface", Call], Tuple[int, Any]]] = {
    "list": Surface._on_list,
    "create": Surface._on_create,
    "get": Surface._on_get,
    "replace": Surface._on_replace,
    "delete": Surface._on_delete,
    "advance": Surface._on_advance,
    "openapi": Surface._on_openapi,
    "health": Surface._on_health,
}


if _handlers_check():
    raise RuntimeError(
        "the handler map and the route table disagree: " + "; ".join(_handlers_check())
    )
