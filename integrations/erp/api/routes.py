"""The route table — derived from the ERP-02 model, not enumerated by hand.

The surface is small on purpose. There is **one** collection route per document
kind rather than one route per kind, and ``{kind}`` is a path parameter whose
value is resolved against ``integrations/erp/core`` — so the closed kind
vocabulary the OpenAPI document declares is the model's own
``DocumentModel.document_kinds()``, and a family added to ERP-02 appears in the
API and in its document with no edit here. A hand-written route list would be a
second description of the model, which is the thing
``integrations/paperclip/api/surface.py`` exists to avoid; this module applies
the same rule to the ERP model.

Two things are therefore *derived* and two are declared:

* derived — the kind vocabulary (the model's kinds) and the transition actions
  (each lifecycle workflow's ``actions_from``, per state);
* declared — the seven route shapes and the platform routes below, which are a
  design decision rather than a fact about the model.

:func:`problems` re-derives the first group and reports a divergence by name, so
a table that stops matching the model fails a gate instead of shipping a
contract that describes something else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from identity.cpapi.router import compile_template

from integrations.erp.auth.model import ACTIONS

__all__ = [
    "ACTION_PARAM",
    "ACTION_PERMISSION",
    "API_PREFIX",
    "DOCUMENTS_PATH",
    "DOCUMENT_ID_PARAM",
    "KIND_PARAM",
    "Match",
    "Route",
    "RouteTable",
    "TABLE",
    "allowed_methods",
    "match",
    "permission_for",
    "problems",
    "transitions",
]

#: The path prefix every route shares.
API_PREFIX = "/v1/erp"
#: The collection root: one pair of routes per document kind.
DOCUMENTS_PATH = API_PREFIX + "/documents"

#: The path parameter names. They are module constants because the OpenAPI
#: document and the router must agree on them, and a string typed twice is a
#: string that can be typed differently.
KIND_PARAM = "kind"
DOCUMENT_ID_PARAM = "documentId"
ACTION_PARAM = "action"

#: The placeholder an action-scoped route carries: the ERP-08 action is the value
#: of ``{action}`` in the path, not a constant of the route.
ACTION_FROM_PATH = f"{{{ACTION_PARAM}}}"

#: A workflow action -> the ERP permission that action requires.
#:
#: The two vocabularies are not the same vocabulary, and collapsing them would be
#: the bug. ERP-02's workflow data names **moves** (``submit``, ``cancel``,
#: ``complete``); ERP-08's ``ACTIONS`` names **permissions** (``submit``,
#: ``cancel``, ``write``, ...). ``submit`` and ``cancel`` are words in both. A
#: move like ``complete`` — the fulfilment step this model's order lifecycle
#: declares and upstream's own sales order has no transition for — is not a
#: permission at all, and asking the auth layer to grant it would be refused as an
#: ``unknown-action`` for every principal alive: a route nobody could ever use.
#: So the mapping is declared here, once, and :func:`problems` refuses an action
#: ERP-02 declares that it does not place.
ACTION_PERMISSION: Mapping[str, str] = {
    "submit": "submit",
    "cancel": "cancel",
    "complete": "write",
}


def permission_for(action: str) -> str:
    """The ERP permission a workflow action requires (empty when unplaced)."""
    return ACTION_PERMISSION.get(action, "")


@dataclass(frozen=True)
class Route:
    """One route of the surface.

    ``name`` is the verb the surface resolves to a handler; ``action`` is the ERP
    action the ERP-08 layer is asked to authorize — either one of the auth
    vocabulary's names or :data:`ACTION_FROM_PATH`.

    ``mutation`` says the route changes the store; ``body_required`` says it
    carries a *document* in its body. They are not the same flag: a transition and
    a delete change the store and carry no document, while a create and a replace
    both do. A route that demanded a body for a transition would refuse every
    well-formed call to it.
    """

    method: str
    template: str
    operation_id: str
    name: str
    action: str
    summary: str
    kind_scoped: bool = True
    mutation: bool = False
    body_required: bool = False

    @property
    def params(self) -> Tuple[str, ...]:
        """The path parameter names this template declares, in order."""
        return tuple(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", self.template))

    @property
    def tag(self) -> str:
        return "documents" if self.kind_scoped else "platform"


#: The declared route shapes. Every kind-scoped route takes ``{kind}`` so the
#: kind vocabulary stays a property of the model rather than of this table.
_DECLARED: Tuple[Route, ...] = (
    Route(
        "GET",
        f"{DOCUMENTS_PATH}/{{{KIND_PARAM}}}",
        "erp.documents.list",
        "list",
        "read",
        "Every document of this kind in the caller's tenant, ordered by id.",
    ),
    Route(
        "POST",
        f"{DOCUMENTS_PATH}/{{{KIND_PARAM}}}",
        "erp.documents.create",
        "create",
        "create",
        "Create a document, validated against its ERP-02 family schema.",
        mutation=True,
        body_required=True,
    ),
    Route(
        "GET",
        f"{DOCUMENTS_PATH}/{{{KIND_PARAM}}}/{{{DOCUMENT_ID_PARAM}}}",
        "erp.documents.get",
        "get",
        "read",
        "One document by id, as this principal is permitted to see it.",
    ),
    Route(
        "PUT",
        f"{DOCUMENTS_PATH}/{{{KIND_PARAM}}}/{{{DOCUMENT_ID_PARAM}}}",
        "erp.documents.replace",
        "replace",
        "write",
        "Replace a document in full; it is re-validated against ERP-02.",
        mutation=True,
        body_required=True,
    ),
    Route(
        "DELETE",
        f"{DOCUMENTS_PATH}/{{{KIND_PARAM}}}/{{{DOCUMENT_ID_PARAM}}}",
        "erp.documents.delete",
        "delete",
        "delete",
        "Remove a document from the tenant's store.",
        mutation=True,
    ),
    Route(
        "POST",
        f"{DOCUMENTS_PATH}/{{{KIND_PARAM}}}/{{{DOCUMENT_ID_PARAM}}}/transitions/{{{ACTION_PARAM}}}",
        "erp.documents.advance",
        "advance",
        ACTION_FROM_PATH,
        "Move a document through a transition its workflow declares.",
        mutation=True,
    ),
    Route("GET", f"{API_PREFIX}/openapi.json", "erp.openapi.get", "openapi", "", "This document.", kind_scoped=False),
    Route("GET", f"{API_PREFIX}/health", "erp.health.get", "health", "", "Health of the dependencies the surface names.", kind_scoped=False),
)


@dataclass(frozen=True)
class Match:
    """A matched request: the route and its captured path parameters."""

    route: Route
    params: Dict[str, str]


class RouteTable:
    """A compiled route table: match by ``(method, path)``, and list methods."""

    def __init__(self, entries: Tuple[Route, ...]) -> None:
        self.entries = tuple(entries)
        self._compiled: Tuple[Tuple[re.Pattern[str], Tuple[str, ...], Route], ...] = tuple(
            (re.compile(pattern), names, route)
            for route in self.entries
            for pattern, names in [compile_template(route.template)]
        )
        # A duplicated (method, template) would make one route unreachable and
        # the document describe two operations for one request — a defect, so it
        # fails at construction rather than being discovered at dispatch time.
        seen: Dict[Tuple[str, str], str] = {}
        for route in self.entries:
            key = (route.method, route.template)
            if key in seen:
                raise ValueError(f"two routes claim {route.method} {route.template}")
            seen[key] = route.operation_id
        if len({route.operation_id for route in self.entries}) != len(self.entries):
            raise ValueError("two routes share an operation id")

    def match(self, method: str, path: str) -> Optional[Match]:
        """The route for ``(method, path)``, or ``None``."""
        if not isinstance(path, str) or not path.startswith("/"):
            return None
        wanted = str(method).upper()
        for pattern, names, route in self._compiled:
            if route.method != wanted:
                continue
            found = pattern.match(path)
            if found is None:
                continue
            return Match(route=route, params=dict(zip(names, found.groups())))
        return None

    def matching_paths(self, path: str) -> Tuple[Route, ...]:
        """Every route whose template matches ``path``, whatever the method."""
        if not isinstance(path, str) or not path.startswith("/"):
            return ()
        return tuple(
            route for pattern, _names, route in self._compiled if pattern.match(path) is not None
        )

    def allowed_methods(self, path: str) -> Tuple[str, ...]:
        """The methods the path accepts, sorted and unique (empty when unknown)."""
        return tuple(sorted({route.method for route in self.matching_paths(path)}))

    def operation_ids(self) -> Tuple[str, ...]:
        return tuple(route.operation_id for route in self.entries)


#: The module's one route table.
TABLE = RouteTable(_DECLARED)


def match(method: str, path: str) -> Optional[Match]:
    return TABLE.match(method, path)


def allowed_methods(path: str) -> Tuple[str, ...]:
    return TABLE.allowed_methods(path)


def transitions(model: object) -> Dict[str, Dict[str, Tuple[str, ...]]]:
    """``kind -> state -> the actions that move it``, from ERP-02's workflows.

    The OpenAPI document carries this map, and :func:`problems` re-derives it, so
    the transition routes cannot describe a move the workflow does not declare.
    Non-lifecycle kinds are absent rather than empty: a kind with no lifecycle has
    no transition route, and an empty entry would read as "its transitions are
    none" rather than "it has none".
    """
    derived: Dict[str, Dict[str, Tuple[str, ...]]] = {}
    for kind in model.lifecycle_kinds():
        workflow = model.workflow_for(kind)
        derived[kind] = {
            state: workflow.actions_from(state) for state in sorted(workflow.state_names())
        }
    return derived


def problems(model: object) -> Tuple[str, ...]:
    """Every way this table contradicts the model it claims to describe.

    Each finding names the kind, the action or the route, so a failure points at
    the artifact rather than at the checker. Empty means the table and the model
    agree.
    """
    found: List[str] = []
    declared_kinds = set(model.document_kinds())

    if not declared_kinds:
        found.append("the model declares no document kind, so the table describes nothing")

    # The transition route is only meaningful if every move ERP-02 declares
    # resolves to a permission the auth layer can grant. An unplaced action is a
    # route no principal could ever reach (the auth layer refuses an unknown
    # action for *everyone*), which is a formality with a URL on it.
    for kind, states in sorted(transitions(model).items()):
        if kind not in declared_kinds:
            found.append(f"the model has a workflow for {kind!r} but declares no document kind")
        for state, actions in sorted(states.items()):
            for action in actions:
                permission = permission_for(action)
                if not permission:
                    found.append(
                        f"workflows: {kind!r} state {state!r} declares the move {action!r}, "
                        "which ACTION_PERMISSION does not place — the auth layer would "
                        "refuse it as an unknown action for every principal"
                    )
                elif permission not in ACTIONS:
                    found.append(
                        f"the move {action!r} maps to {permission!r}, which is not an ERP "
                        f"permission ({', '.join(ACTIONS)})"
                    )

    # Every declared route must be reachable: its template must name the params
    # its handler reads, and an action-scoped route must be an action path.
    for route in TABLE.entries:
        if route.kind_scoped and KIND_PARAM not in route.params:
            found.append(
                f"route {route.operation_id!r} is kind-scoped but its template "
                f"declares no {{{KIND_PARAM}}} parameter"
            )
        if route.action == ACTION_FROM_PATH and ACTION_PARAM not in route.params:
            found.append(
                f"route {route.operation_id!r} takes its action from the path but "
                f"declares no {{{ACTION_PARAM}}} parameter"
            )
        if not route.kind_scoped and route.action:
            found.append(
                f"route {route.operation_id!r} is not kind-scoped but names the "
                f"action {route.action!r}"
            )
        if route.action and route.action != ACTION_FROM_PATH and route.action not in ACTIONS:
            found.append(
                f"route {route.operation_id!r} asks for {route.action!r}, which is not an "
                f"ERP permission ({', '.join(ACTIONS)})"
            )
    return tuple(found)
