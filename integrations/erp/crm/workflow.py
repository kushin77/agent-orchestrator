"""The document state machines (issue #650, acceptance criterion 1).

Every state change in this package goes through :func:`advance`, and every
:func:`advance` ends on the audit rail. There is no other way to change a
document's state: :class:`~.model.Document` is frozen, so a caller holding one
cannot write a new state into it — it can only ask the machine for a transition,
be refused if the declaration does not license it, and receive the extended
rail that records it. That is what "state-machine-driven and audited" means
mechanically rather than as a promise.

Three refusals, and why each is a real failure mode:

* ``unknown-state`` — the target is not a state of this kind at all. That is a
  caller bug (a typo, or a state borrowed from a sibling kind), and the message
  names the kind's own states.
* ``illegal-transition`` — the target *is* a state but the current state does
  not reach it. The message names both ends and the states that *are* reachable,
  so the caller is told the legal move rather than merely that this one is
  wrong. A transition declared as `[]` (a terminal state) refuses here too,
  which is how "a closed project accepts no new work" is enforced by the same
  code path as any other illegal move.
* ``unknown-action`` — the audit action is not in the closed vocabulary
  (raised by ``audit.Rail.append``).
"""

from __future__ import annotations

from typing import Tuple

from . import audit
from .definitions import DefinitionSet
from .model import ACTION_ADVANCE, Document, Refused


def transition_targets(document: Document, definitions: DefinitionSet) -> Tuple[str, ...]:
    """The states this document may move to *now*, sorted for determinism."""
    declared = definitions.kind(document.kind)
    return tuple(sorted(declared.transitions.get(document.state, ())))


def assert_transition(document: Document, target: str, definitions: DefinitionSet) -> None:
    """Refuse unless ``document`` may legally move to ``target`` now."""
    declared = definitions.kind(document.kind)
    if not declared.is_state(target):
        raise Refused(
            "unknown-state",
            f"{document.id}: {target!r} is not a state of a {document.kind} "
            f"(declared: {', '.join(declared.states)})",
        )
    if not declared.allows(document.state, target):
        reachable = transition_targets(document, definitions)
        raise Refused(
            "illegal-transition",
            f"{document.id}: a {document.kind} in {document.state!r} cannot move to "
            f"{target!r} (reachable now: "
            + (", ".join(reachable) if reachable else "none — this is a terminal state")
            + ")",
        )


def is_terminal(document: Document, definitions: DefinitionSet) -> bool:
    """Whether the declaration gives this state no outgoing transition."""
    return not transition_targets(document, definitions)


def accepts_child_work(document: Document, definitions: DefinitionSet) -> bool:
    """Whether a document in this state still accepts child work (timesheets).

    Read from the kind's declared ``openStates``, and fail-closed for a kind
    that declares none: a family that forgot to declare its open states does not
    silently accept work against closed parents.
    """
    return definitions.kind(document.kind).is_open(document.state)


def advance(
    document: Document,
    target: str,
    *,
    actor: str,
    at: str,
    definitions: DefinitionSet,
    rail: audit.Rail,
    note: str = "",
    action: str = ACTION_ADVANCE,
) -> Tuple[Document, audit.Rail]:
    """Move a document to ``target`` and record the transition; refuse otherwise."""
    assert_transition(document, target, definitions)
    moved = document.with_state(target)
    extended = rail.append(
        at=at,
        actor=actor,
        action=action,
        kind=moved.kind,
        ref=moved.id,
        from_state=document.state,
        to_state=target,
        note=note,
    )
    return moved, extended
