"""The approvals projection model (issue #416).

Everything here is stdlib-only, offline and deterministic. There is deliberately
**no store, no schema-migration and no write helper**: an approval is a value
derived from an authoritative record, and the only way to change its state is to
change the authority it points at.

---knowledge---
module_id: integrations.paperclip.adapters.approvals.model
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [ApprovalRefused, Authority, authority_for, Request, Decision, Approval, Finding, Projection]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

# --------------------------------------------------------------------------
# Kinds — each one names an authority (EPIC #410, issue #416)
# --------------------------------------------------------------------------

KIND_HIRE = "hire"
KIND_TOP_UP = "top-up"
KIND_OVERRIDE = "override"
KINDS: Tuple[str, ...] = (KIND_HIRE, KIND_TOP_UP, KIND_OVERRIDE)

# --------------------------------------------------------------------------
# States — `pending` is distinguishable from a decision, by construction
# --------------------------------------------------------------------------

STATE_PENDING = "pending"
STATE_GRANTED = "granted"
STATE_DENIED = "denied"
STATES: Tuple[str, ...] = (STATE_PENDING, STATE_GRANTED, STATE_DENIED)

# --------------------------------------------------------------------------
# Roles — the fleet hierarchy is the authorisation to decide
# --------------------------------------------------------------------------

ROLE_OPERATOR = "operator"
ROLE_BRAIN = "brain"
ROLE_AGENT = "agent"
#: The full fleet role vocabulary (the channel's own set). `sister` and
#: `subagent` are real fleet roles — they are simply not *deciders* for any
#: approval kind, which the authority check reports by name.
ROLES: Tuple[str, ...] = (ROLE_OPERATOR, ROLE_BRAIN, ROLE_AGENT, "sister", "subagent")

# Decision outcomes on an authoritative record.
DECISION_GRANT = "grant"
DECISION_DENY = "deny"
DECISIONS: Tuple[str, ...] = (DECISION_GRANT, DECISION_DENY)


class ApprovalRefused(Exception):
    """An approval projection or decision was refused.

    ``reason`` is the machine-readable code (the gate asserts on it); ``detail``
    names the offender — the kind, the subject, the record — so a failure names
    *what* was refused rather than merely that something was.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Authority:
    """The single authoritative surface a kind's decisions are read from.

    ``deciders`` is the set of roles the fleet hierarchy grants the authority to
    decide this kind. A decision whose actor role is not in this set is refused:
    the adapter cannot grant what the fleet did not grant (ADR-0013 /
    repo governance rules), and neither can an actor the fleet never empowered.
    """

    kind: str
    surface: str
    store: str
    deciders: Tuple[str, ...]
    grant_event: str = ""
    deny_event: str = ""

    def may_decide(self, role: str) -> bool:
        return role in self.deciders

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "surface": self.surface,
            "store": self.store,
            "deciders": list(self.deciders),
            "grant_event": self.grant_event,
            "deny_event": self.deny_event,
        }


#: The kind -> authority map. This is the whole contract of the adapter: a kind
#: that is not a key here has **no authority behind it** and is refused, never
#: defaulted.
AUTHORITIES: Dict[str, Authority] = {
    KIND_HIRE: Authority(
        kind=KIND_HIRE,
        surface="governance/dispatch",
        store=".board/claims/*.json",
        deciders=(ROLE_AGENT,),
        grant_event="claim",
        deny_event="reap",
    ),
    KIND_TOP_UP: Authority(
        kind=KIND_TOP_UP,
        surface=".fleet/sent",
        store=".fleet/sent/*.json",
        deciders=(ROLE_BRAIN,),
        grant_event="approval-grant",
        deny_event="approval-deny",
    ),
    KIND_OVERRIDE: Authority(
        kind=KIND_OVERRIDE,
        surface="fleet/control.py",
        store=".fleet/sent/*.json",
        deciders=(ROLE_OPERATOR,),
        grant_event="control-override",
        deny_event="approval-deny",
    ),
}


def authority_for(kind: str) -> Authority:
    """The authority a kind projects onto, or a refusal naming the kind.

    A kind with no authority behind it is refused here rather than mapped to a
    default — the projection cannot invent the authority it is supposed to
    reflect.
    """
    authority = AUTHORITIES.get(kind)
    if authority is None:
        raise ApprovalRefused(
            "no-authority",
            f"approval kind {kind!r} has no authoritative surface "
            f"(known kinds: {', '.join(KINDS)})",
        )
    return authority


@dataclass(frozen=True)
class Request:
    """A *request* for a privileged action, projected from a request surface.

    A request carries no decision. Its only effect is to make a ``pending``
    approval visible: without a matching authoritative decision record the
    request can never become ``granted``.
    """

    kind: str
    subject: str
    ref: str
    requested_by: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "ref": self.ref,
            "requested_by": self.requested_by,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Decision:
    """A *decision* read from an authority — the only thing that grants.

    ``surface`` and ``ref`` name exactly where the decision lives, so a
    projection can always be re-verified against it and a projection that cites
    a record the authority does not hold fails.
    """

    kind: str
    subject: str
    decision: str
    actor: str
    role: str
    surface: str
    ref: str
    evidence: str = ""

    @property
    def granted(self) -> bool:
        return self.decision == DECISION_GRANT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "decision": self.decision,
            "actor": self.actor,
            "role": self.role,
            "surface": self.surface,
            "ref": self.ref,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class Approval:
    """The projected approval object.

    ``state`` is *derived*: ``pending`` exactly when no decision record exists.
    For a decided approval ``decision_ref`` and ``authority`` name the record
    the state was read from, which is what ``verify`` re-checks — a projection
    that claims a state its authority does not back is a bug, not a grant.
    """

    id: str
    kind: str
    subject: str
    state: str
    authority: str
    authority_store: str
    actor: str = ""
    role: str = ""
    decision: str = ""
    decision_ref: str = ""
    request_ref: str = ""
    requested_by: str = ""
    reason: str = ""

    @property
    def pending(self) -> bool:
        return self.state == STATE_PENDING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "state": self.state,
            "authority": self.authority,
            "authority_store": self.authority_store,
            "actor": self.actor,
            "role": self.role,
            "decision": self.decision,
            "decision_ref": self.decision_ref,
            "request_ref": self.request_ref,
            "requested_by": self.requested_by,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Finding:
    """A verification finding — a refusal, with the offender named."""

    code: str
    detail: str
    ref: str = ""
    kind: str = ""
    subject: str = ""

    def line(self) -> str:
        where = f"{self.kind}/{self.subject}" if self.kind else (self.ref or "")
        return f"{self.code}: {self.detail}" + (f" [{where}]" if where else "")


@dataclass
class Projection:
    """The whole projected set plus the refusals encountered while building it."""

    approvals: Tuple[Approval, ...] = ()
    findings: Tuple[Finding, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approvals": [a.to_dict() for a in self.approvals],
            "findings": [
                {"code": f.code, "detail": f.detail, "ref": f.ref, "kind": f.kind, "subject": f.subject}
                for f in self.findings
            ],
        }
