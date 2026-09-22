"""Typed shapes for the paperclip.ing integration adapter (issue #428).

Two families of shapes live here, deliberately kept apart:

* **our-side source shapes** — ``Profile`` (a ``registry/profiles/seeds`` seed),
  ``Persona`` (a ``registry/personas/cards`` card), ``Budget`` (a row of the
  fleet budget rail) and ``Control`` (an operator verb over ``fleet/control.py``);
* **upstream resource shapes** — ``Agent``, ``Issue`` (the ticket seam record),
  ``Cost`` (the budget seam record), ``Approval`` and ``Activity`` — the records
  the upstream ``/api`` surface carries and the shapes the mapper emits.

Stdlib-only by construction (frozen dataclasses + typing), so neither the tests
nor the gate pull a third-party dependency in.

---knowledge---
module_id: integrations.paperclip.model
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [PaperclipError, ValidationError, AuthError, ForbiddenError, NotFoundError, ConflictError, UnprocessableError, ServiceUnavailableError, (+10 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .._seam.wire import Response  # noqa: F401 - re-exported at this adapter's seam
from .._seam.wire import WireBoundary

# --------------------------------------------------------------------------
# Transport primitives
# --------------------------------------------------------------------------
# ``Response`` and the rendering of ``error_for_status`` are the shared seam's
# (``integrations/_seam/wire.py``, issue #1208): both were byte-identical in this
# adapter and in ``integrations/hermes/``, so they live there now and are
# re-exported here — ``from integrations.paperclip.model import Response`` keeps
# working. What stays local is this adapter's own vocabulary: the error classes
# below and the boundary that binds them to the seam's rendering.


class PaperclipError(Exception):
    """Base error for the paperclip.ing adapter."""

    #: HTTP status this error is mapped from.
    status = 0

    def __init__(self, message: str, *, status: Optional[int] = None, path: str = "") -> None:
        super().__init__(message)
        self.path = path
        if status is not None:
            self.status = status


class ValidationError(PaperclipError):
    """``400`` — the request failed validation."""

    status = 400


class AuthError(PaperclipError):
    """``401`` — the caller identity is missing or invalid."""

    status = 401


class ForbiddenError(PaperclipError):
    """``403`` — the caller is known but not allowed."""

    status = 403


class NotFoundError(PaperclipError):
    """``404`` — the resource is missing or outside the company scope."""

    status = 404


class ConflictError(PaperclipError):
    """``409`` — the resource is owned, locked, or in conflict."""

    status = 409


class UnprocessableError(PaperclipError):
    """``422`` — the request is well-formed but breaks a business rule."""

    status = 422


class ServiceUnavailableError(PaperclipError):
    """``503`` — the upstream database is unreachable."""

    status = 503


#: The documented status -> typed-error mapping (ADR-0013, seam doc §1).
ERROR_BY_STATUS: Dict[int, type] = {
    400: ValidationError,
    401: AuthError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableError,
    503: ServiceUnavailableError,
}

#: This adapter's wire vocabulary, bound to its own typed errors: the shared
#: seam renders the message and constructs the error (``_seam/wire.py``).
BOUNDARY = WireBoundary(
    label="paperclip", error_by_status=ERROR_BY_STATUS, base_error=PaperclipError
)


def error_for_status(status: int, path: str, detail: str = "") -> PaperclipError:
    """Build the typed error a given upstream status maps to.

    The status→class table and the ``paperclip`` label are this adapter's; the
    message shape and the constructor call are the shared seam's.
    """
    return BOUNDARY.error_for_status(status, path, detail)


# --------------------------------------------------------------------------
# Our-side source shapes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """A ``registry/profiles/seeds/*.yaml`` AgentProfile seed."""

    id: str
    version: str
    owner: str
    default_model_tier: str
    capabilities: Tuple[str, ...] = ()
    tools: Tuple[str, ...] = ()
    constraints: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Persona:
    """A ``registry/personas/cards/*.yaml`` PersonaCard."""

    id: str
    name: str
    summary: str
    posture: str
    tier: str
    owned_lanes: Tuple[str, ...] = ()
    expertise: Tuple[str, ...] = ()
    reports_to: str = ""
    """The card's declared ``reportsTo`` (registry/personas/persona-card.schema.json),
    e.g. ``cto`` reports to ``ceo``. Empty when the card declares none (issue #1573:
    lets the org adapter project the real CEO -> CTO -> SME hierarchy instead of
    flattening every agent under the default owner)."""


@dataclass(frozen=True)
class Budget:
    """A row of the fleet budget rail (a per-scope cap over a period)."""

    scope_level: str
    scope_id: str
    period: str
    cap: float
    spent: float
    currency: str
    hard_stop: bool
    burn_rate_alert_pct: float
    receipt_ref: str


@dataclass(frozen=True)
class Control:
    """An operator verb over ``fleet/control.py`` (pause / resume / kill)."""

    verb: str
    target: str


# --------------------------------------------------------------------------
# Upstream resource shapes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Agent:
    """An upstream agent record — the org-chart entry the mapper emits.

    The seam doc's rule is decisive: *if it can receive a heartbeat, it is
    hired*. A seed or a persona card that declares a mappable identity is hired;
    ``reports_to`` carries the reporting line.
    """

    agent_id: str
    name: str
    role: str
    reports_to: str
    hired: bool
    tier: str
    capabilities: Tuple[str, ...] = ()
    kind: str = "persona"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "reports_to": self.reports_to,
            "hired": self.hired,
            "tier": self.tier,
            "capabilities": list(self.capabilities),
            "kind": self.kind,
        }


@dataclass(frozen=True)
class Issue:
    """An upstream issue — the **ticket** seam record (``ticket.schema.json``).

    Only the schema's own six keys are emitted; ``to_dict`` is the exact shape
    the schema validates, so the two cannot drift.
    """

    id: str
    owner: str
    status: str
    blocked_by: Tuple[str, ...]
    goal: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner,
            "status": self.status,
            "blocked_by": list(self.blocked_by),
            "goal": self.goal,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class Cost:
    """An upstream cost line — the **budget** seam record (``budget.schema.json``)."""

    scope_level: str
    scope_id: str
    period: str
    cap: float
    spent: float
    currency: str
    hard_stop: bool
    burn_rate_alert_pct: float
    receipt_ref: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": {"level": self.scope_level, "id": self.scope_id},
            "period": self.period,
            "cap": self.cap,
            "spent": self.spent,
            "currency": self.currency,
            "hard_stop": self.hard_stop,
            "burn_rate_alert_pct": self.burn_rate_alert_pct,
            "receipt_ref": self.receipt_ref,
        }


@dataclass(frozen=True)
class Approval:
    """An upstream approval — a budget top-up or a policy exception."""

    id: str
    kind: str
    scope_level: str
    scope_id: str
    amount: float
    state: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "scope": {"level": self.scope_level, "id": self.scope_id},
            "amount": self.amount,
            "state": self.state,
        }


@dataclass(frozen=True)
class Activity:
    """An upstream activity entry — the append-only audit surface."""

    id: str
    actor: str
    verb: str
    object_ref: str
    ts: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "actor": self.actor,
            "verb": self.verb,
            "object_ref": self.object_ref,
            "ts": self.ts,
        }
