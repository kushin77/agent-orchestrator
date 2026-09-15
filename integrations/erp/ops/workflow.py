"""State changes: ERP-02's state machine, this lane's audit rail.

``advance`` is the only way a document's state changes. It asks ERP-02's
workflow data model which state an action reaches — so a state jump is refused
by the same rule the core model uses, with the same ``state_jumped`` detail —
and it writes the move to the append-only rail in the same call, so a document
cannot be advanced without the move being recorded. There is no second path:
the returned document *is* the state change and the returned rail *is* its
record.

``docstatus`` is derived from the workflow rather than passed in. The two cannot
disagree, because there is only one of them.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from .audit import Rail
from .model import ACTION_ADVANCE, Model, Refused

__all__ = ["advance", "docstatus_for", "targets"]


def docstatus_for(model: Model, kind: str, state: str) -> int:
    """The ``docstatus`` the workflow declares for ``state``."""
    return model.workflow_for(kind).state(state).docstatus


def targets(model: Model, document: Mapping[str, Any]) -> Tuple[str, ...]:
    """Every state ``document`` can reach from where it is."""
    kind = document.get("doctype")
    state = document.get("state")
    if not isinstance(kind, str) or not isinstance(state, str):
        return ()
    workflow = model.workflow_for(kind)
    return tuple(sorted(set(workflow.legal_targets(state))))


def advance(
    model: Model,
    document: Mapping[str, Any],
    action: str,
    *,
    actor: str,
    at: str,
    rail: Rail,
    target: Optional[str] = None,
) -> Tuple[Dict[str, Any], Rail]:
    """Move ``document`` by ``action``, recording the move on ``rail``.

    Returns the advanced document and the extended rail. Refuses by name (through
    the model) when the action is not available from the document's current
    state, or when ``target`` asserts a state the action does not reach.
    """
    kind = document.get("doctype")
    if not isinstance(kind, str) or not kind.strip():
        raise Refused(
            "unknown-kind",
            f"document {document.get('id')!r} declares no doctype, so no workflow applies",
        )
    moved = model.advance(document, action, target=target)
    advanced = dict(document)
    advanced["state"] = moved
    advanced["docstatus"] = docstatus_for(model, kind, moved)
    extended = rail.append(
        at=at,
        actor=actor,
        action=ACTION_ADVANCE,
        document=str(document.get("id")),
        detail=f"{kind} {document.get('id')}: {document.get('state')} -> {moved} ({action})",
    )
    return advanced, extended
