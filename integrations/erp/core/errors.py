"""ERP document-model refusals, on the house error taxonomy (#647).

The core document model does not invent an error convention: refusals and
responses reuse the control-plane taxonomy and envelope that
``identity/cpapi`` already ships (`errors.ApiError`, `router.ok_envelope` /
`router.error_envelope`, the ``{ok,status,requestId,data,error}`` shape), so a
later ERP REST surface (ERP-06, #651) mounts these validators without a
translation layer and a client that already speaks the house envelope needs no
second dialect.

Two things this module adds, and nothing else:

* :class:`ErpError` — the same ``status/code/message/details`` record, so an
  ERP refusal is distinguishable from a control-plane one at the type level
  while remaining envelope-compatible;
* the closed **code vocabulary** for the document model, declared as constants
  so a caller branches on a name rather than on prose, and so a test can assert
  the vocabulary is closed.

The status grouping mirrors ``identity/cpapi/errors.py``: ``400`` a
structurally invalid body, ``404`` an unknown document kind or workflow,
``409`` a conflict with the document's current state, ``422`` a fail-closed
refusal (the data itself is unusable, so no verdict about the document can be
issued), ``503`` a missing capability rather than a bad request.

---knowledge---
module_id: integrations.erp.core.errors
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ErpError, invalid_body, schema_violation, unknown_document_kind, unknown_workflow, unknown_state, unknown_action, state_jumped, (+5 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from identity.cpapi.errors import ApiError
from identity.cpapi.router import error_envelope, ok_envelope

__all__ = [
    "CODES",
    "ErpError",
    "error_envelope",
    "invalid_body",
    "missing_provenance",
    "ok_envelope",
    "schema_violation",
    "state_jumped",
    "unbalanced_posting",
    "invalid_transfer",
    "unknown_action",
    "unknown_document_kind",
    "unknown_state",
    "unknown_workflow",
    "workflow_invalid",
    "yaml_unavailable",
]

#: The closed refusal vocabulary of the core document model.
CODES = (
    "invalid_body",         # 400 — not a mapping at all
    "schema_violation",     # 400 — a document that does not validate
    "unknown_document_kind",  # 404 — no schema is registered for this doctype
    "unknown_workflow",     # 404 — no workflow is registered for this doctype
    "unknown_state",        # 409 — the document declares a state the workflow lacks
    "unknown_action",       # 409 — the workflow has no such action from this state
    "state_jumped",         # 409 — the move is not a declared transition
    "workflow_invalid",     # 422 — the workflow data cannot be interpreted
    "missing_provenance",   # 422 — a schema without its harvest record
    "unbalanced_posting",   # 422 — a GL posting whose debits do not equal credits
    "invalid_transfer",     # 422 — a stock transfer that moves nothing
    "yaml_unavailable",     # 503 — no YAML parser, so the data cannot be read
)


class ErpError(ApiError):
    """One ERP document-model refusal. Envelope-compatible with ``ApiError``."""


def _refusal(status: int, code: str, message: str, details: Optional[Dict[str, Any]]):
    return ErpError(status, code, message, details or None)


def invalid_body(message: str, **details: Any) -> ErpError:
    """The input is not a document mapping at all (400)."""
    return _refusal(400, "invalid_body", message, details)


def schema_violation(message: str, **details: Any) -> ErpError:
    """The document does not validate against its family schema (400)."""
    return _refusal(400, "schema_violation", message, details)


def unknown_document_kind(kind: str, known: Any = None) -> ErpError:
    """No schema is registered for this document kind (404)."""
    details: Dict[str, Any] = {"kind": kind}
    if known is not None:
        details["known"] = list(known)
    return _refusal(404, "unknown_document_kind", f"unknown document kind {kind!r}", details)


def unknown_workflow(document: str, known: Any = None) -> ErpError:
    """No workflow is registered for this document (404)."""
    details: Dict[str, Any] = {"document": document}
    if known is not None:
        details["known"] = list(known)
    return _refusal(404, "unknown_workflow", f"no workflow for {document!r}", details)


def unknown_state(state: str, workflow: str, known: Any = None) -> ErpError:
    """The document declares a state its workflow does not define (409)."""
    details: Dict[str, Any] = {"state": state, "workflow": workflow}
    if known is not None:
        details["known"] = list(known)
    return _refusal(
        409, "unknown_state", f"{workflow!r} defines no state {state!r}", details
    )


def unknown_action(action: str, state: str, workflow: str, known: Any = None) -> ErpError:
    """The workflow has no such action from the document's current state (409)."""
    details: Dict[str, Any] = {"action": action, "state": state, "workflow": workflow}
    if known is not None:
        details["available"] = list(known)
    return _refusal(
        409,
        "unknown_action",
        f"{workflow!r} has no action {action!r} from state {state!r}",
        details,
    )


def state_jumped(
    workflow: str, from_state: str, to_state: str, legal: Any = None
) -> ErpError:
    """The move is not a declared transition — a state jump (409)."""
    details: Dict[str, Any] = {
        "workflow": workflow,
        "from": from_state,
        "to": to_state,
    }
    if legal is not None:
        details["legal"] = list(legal)
    return _refusal(
        409,
        "state_jumped",
        f"{workflow!r} does not allow a move from {from_state!r} to {to_state!r}",
        details,
    )


def workflow_invalid(message: str, **details: Any) -> ErpError:
    """The workflow data itself cannot be interpreted (422)."""
    return _refusal(422, "workflow_invalid", message, details)


def missing_provenance(message: str, **details: Any) -> ErpError:
    """A schema is missing the harvest record the doctrine requires (422)."""
    return _refusal(422, "missing_provenance", message, details)


def unbalanced_posting(message: str, **details: Any) -> ErpError:
    """A GL posting whose debits and credits do not agree (422)."""
    return _refusal(422, "unbalanced_posting", message, details)


def invalid_transfer(message: str, **details: Any) -> ErpError:
    """A stock transfer that moves nothing (422)."""
    return _refusal(422, "invalid_transfer", message, details)


def yaml_unavailable(message: str, **details: Any) -> ErpError:
    """No YAML parser is importable, so the workflow data cannot be read (503)."""
    return _refusal(503, "yaml_unavailable", message, details)
