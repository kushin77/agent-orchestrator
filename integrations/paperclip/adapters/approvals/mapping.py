"""The deterministic approvals projection (issue #416).

The projector is a pure function of the tree it is handed. It reads three of the
fleet's *existing* surfaces and derives approvals — it writes nothing.

Inputs (read-only):

* ``<root>/.fleet/brain/inbox/*.json`` — operator orders carrying an ``approval``
  request (a *request*: no decision, so any approval built from it is ``pending``);
* ``<root>/.board/claims/*.json``    — the ``governance/dispatch`` claim ledger
  (the authority for a **hire**);
* ``<root>/.fleet/sent/*.json``      — the brain directive ledger (the authority
  for a **top-up** and for an **override** issued through the control verb).

Same input, byte-identical output. An unmappable record is never defaulted: it
becomes a finding naming the offender (fail closed).

---knowledge---
module_id: integrations.paperclip.adapters.approvals.mapping
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [load_requests, authority_for_safe, load_decisions, project, render, decision_index]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .model import (
    DECISION_DENY,
    DECISION_GRANT,
    KIND_HIRE,
    KIND_OVERRIDE,
    KIND_TOP_UP,
    ROLES,
    ROLE_AGENT,
    ROLE_OPERATOR,
    STATE_DENIED,
    STATE_GRANTED,
    STATE_PENDING,
    Approval,
    ApprovalRefused,
    Decision,
    Finding,
    Projection,
    Request,
    authority_for,
)

#: Where each read-only surface lives, relative to the root.
REQUESTS_GLOB = ".fleet/brain/inbox/*.json"
CLAIMS_GLOB = ".board/claims/*.json"
SENT_GLOB = ".fleet/sent/*.json"

#: The additive envelope key carrying an approval request/decision on a fleet
#: message. Additive: ``fleet/channel.validate`` checks the fields it knows and
#: tolerates an extra one, so an order may carry it without ceasing to be a
#: valid directive.
APPROVAL_KEY = "approval"

#: The claim-ledger events that are approval decisions. ``release`` closes a
#: live claim but decides nothing, so it is deliberately absent.
CLAIM_GRANT_EVENTS: Tuple[str, ...] = ("claim", "take-over")
CLAIM_DENY_EVENTS: Tuple[str, ...] = ("reap",)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _glob_json(root: Path, pattern: str) -> List[Tuple[str, Dict[str, Any], Finding | None]]:
    """Every JSON object under ``pattern``, sorted by name, with read errors kept.

    A malformed record is not skipped silently: it is returned as a finding so
    the projection reports it (no silent skips).
    """
    records: List[Tuple[str, Dict[str, Any], Finding | None]] = []
    for path in sorted(root.glob(pattern)):
        ref = _relative(root, path)
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            records.append((ref, {}, Finding("malformed-record", f"{ref} is unreadable ({exc})", ref=ref)))
            continue
        if not isinstance(data, dict):
            records.append((ref, {}, Finding("malformed-record", f"{ref} is not a JSON object", ref=ref)))
            continue
        records.append((ref, data, None))
    return records


# --------------------------------------------------------------------------
# Requests  (`.fleet/brain/inbox`)
# --------------------------------------------------------------------------


def load_requests(root: Path, findings: List[Finding]) -> List[Request]:
    """Read approval *requests* from the operator's order mailbox."""
    requests: List[Request] = []
    for ref, data, problem in _glob_json(root, REQUESTS_GLOB):
        if problem is not None:
            findings.append(problem)
            continue
        marker = data.get(APPROVAL_KEY)
        if marker is None:
            continue
        if not isinstance(marker, dict):
            findings.append(Finding("malformed-request", f"{ref}: {APPROVAL_KEY} must be an object", ref=ref))
            continue
        kind = marker.get("kind")
        subject = marker.get("subject")
        if not kind or not subject:
            findings.append(
                Finding(
                    "malformed-request",
                    f"{ref}: request must name both 'kind' and 'subject'",
                    ref=ref,
                )
            )
            continue
        if not authority_for_safe(kind, findings, ref):
            continue
        requests.append(
            Request(
                kind=str(kind),
                subject=str(subject),
                ref=ref,
                requested_by=str(marker.get("requested_by", "") or ""),
                reason=str(marker.get("reason", "") or ""),
            )
        )
    return requests


def authority_for_safe(kind: str, findings: List[Finding], ref: str) -> bool:
    """Refuse a kind with no authority behind it, as a finding naming the kind."""
    try:
        authority_for(kind)
    except ApprovalRefused as exc:
        findings.append(Finding(exc.reason, exc.detail, ref=ref, kind=str(kind)))
        return False
    return True


# --------------------------------------------------------------------------
# Decisions  (`.board/claims` + `.fleet/sent`)
# --------------------------------------------------------------------------


def _decide_from_claim(ref: str, data: Dict[str, Any], findings: List[Finding]) -> Decision | None:
    event = data.get("event")
    issue = data.get("issue")
    agent = data.get("agent")
    if not isinstance(event, str) or not isinstance(issue, int) or isinstance(issue, bool):
        findings.append(Finding("malformed-record", f"{ref}: claim record needs 'event' and 'issue'", ref=ref))
        return None
    if event in CLAIM_GRANT_EVENTS:
        decision = DECISION_GRANT
    elif event in CLAIM_DENY_EVENTS:
        decision = DECISION_DENY
    else:
        return None
    return Decision(
        kind=KIND_HIRE,
        subject=f"issue:{issue}",
        decision=decision,
        actor=str(agent or ""),
        role=ROLE_AGENT,
        surface=authority_for(KIND_HIRE).surface,
        ref=ref,
        evidence=event,
    )


def _decide_from_directive(ref: str, data: Dict[str, Any], findings: List[Finding]) -> Decision | None:
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    marker = data.get(APPROVAL_KEY)
    marker = marker if isinstance(marker, dict) else {}

    # Override — the control verb's own shape: a directive carrying
    # `control == "override"`. This exists on the fleet today (`fleet/brain.py`
    # `build_directive`, ordered by `fleet/control.py override`), so the override
    # path needs no additive field: the actor is the operator who ordered it.
    if data.get("control") == "override":
        subject = str(marker.get("subject") or f"issue:{task.get('issue')}")
        decision = str(marker.get("decision") or DECISION_GRANT)
        actor = str(marker.get("actor") or ROLE_OPERATOR)
        if decision not in (DECISION_GRANT, DECISION_DENY):
            findings.append(Finding("malformed-record", f"{ref}: override decision {decision!r} is not grant/deny", ref=ref))
            return None
        return Decision(
            kind=KIND_OVERRIDE,
            subject=subject,
            decision=decision,
            actor=actor,
            role=ROLE_OPERATOR,
            surface=authority_for(KIND_OVERRIDE).surface,
            ref=ref,
            evidence="control-override",
        )

    # Top-up — a directive carrying a top-up decision marker. The marker is the
    # declared encoding of the top-up on the directive ledger; the *producer*
    # (the brain emitting a top-up directive) is adoption work (seam doc §5.7).
    if marker.get("kind") == KIND_TOP_UP:
        subject = marker.get("subject")
        decision = marker.get("decision")
        actor = marker.get("actor")
        if not subject or not actor or decision not in (DECISION_GRANT, DECISION_DENY):
            findings.append(
                Finding(
                    "malformed-record",
                    f"{ref}: top-up needs 'subject', 'decision' (grant|deny) and an actor 'actor'",
                    ref=ref,
                    kind=KIND_TOP_UP,
                )
            )
            return None
        return Decision(
            kind=KIND_TOP_UP,
            subject=str(subject),
            decision=str(decision),
            actor=str(actor),
            role=str(actor),
            surface=authority_for(KIND_TOP_UP).surface,
            ref=ref,
            evidence="approval-top-up",
        )
    return None


def load_decisions(root: Path, findings: List[Finding]) -> List[Decision]:
    """Read every authoritative *decision* — the only thing that grants."""
    decisions: List[Decision] = []
    for ref, data, problem in _glob_json(root, CLAIMS_GLOB):
        if problem is not None:
            findings.append(problem)
            continue
        decided = _decide_from_claim(ref, data, findings)
        if decided is not None:
            decisions.append(decided)
    for ref, data, problem in _glob_json(root, SENT_GLOB):
        if problem is not None:
            findings.append(problem)
            continue
        decided = _decide_from_directive(ref, data, findings)
        if decided is not None:
            decisions.append(decided)
    return decisions


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------


def _approval_id(kind: str, subject: str) -> str:
    return f"approval-{kind}-{subject.replace(':', '-').replace('/', '-')}"


def _check_decisions(decisions: Sequence[Decision], findings: List[Finding]) -> None:
    """Refuse a double approval, a conflicting pair, and an unauthorised decider."""
    grants: Dict[Tuple[str, str], List[Decision]] = {}
    denies: Dict[Tuple[str, str], List[Decision]] = {}
    for decision in decisions:
        key = (decision.kind, decision.subject)
        (grants if decision.granted else denies).setdefault(key, []).append(decision)
        authority = authority_for(decision.kind)
        if decision.role not in ROLES:
            findings.append(
                Finding(
                    "unauthorised-decider",
                    f"{decision.ref}: actor role {decision.role!r} is not a fleet role",
                    ref=decision.ref,
                    kind=decision.kind,
                    subject=decision.subject,
                )
            )
        elif not authority.may_decide(decision.role):
            findings.append(
                Finding(
                    "unauthorised-decider",
                    f"{decision.ref}: {decision.actor!r} ({decision.role}) has no authority to "
                    f"decide a {decision.kind} (authority: {authority.surface}; "
                    f"deciders: {', '.join(authority.deciders)})",
                    ref=decision.ref,
                    kind=decision.kind,
                    subject=decision.subject,
                )
            )
    for key, group in sorted(grants.items()):
        if len(group) > 1:
            refs = ", ".join(sorted(d.ref for d in group))
            findings.append(
                Finding(
                    "double-approval",
                    f"{key[0]}/{key[1]} is granted {len(group)} times ({refs}) — an item is approved once",
                    ref=refs,
                    kind=key[0],
                    subject=key[1],
                )
            )
    for key in sorted(set(grants) & set(denies)):
        findings.append(
            Finding(
                "conflicting-decision",
                f"{key[0]}/{key[1]} carries both a grant and a deny",
                kind=key[0],
                subject=key[1],
            )
        )


def project(root: Path | str) -> Projection:
    """Derive every approval from the tree at ``root``. Deterministic; read-only."""
    root = Path(root)
    findings: List[Finding] = []
    requests = load_requests(root, findings)
    decisions = load_decisions(root, findings)
    _check_decisions(decisions, findings)

    by_subject: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for request in requests:
        entry = by_subject.setdefault((request.kind, request.subject), {"request": request, "decisions": []})
        entry["request"] = request
    for decision in decisions:
        entry = by_subject.setdefault((decision.kind, decision.subject), {"request": None, "decisions": []})
        entry["decisions"].append(decision)

    approvals: List[Approval] = []
    for (kind, subject) in sorted(by_subject):
        entry = by_subject[(kind, subject)]
        authority = authority_for(kind)
        group: List[Decision] = sorted(entry["decisions"], key=lambda d: d.ref)
        request: Request | None = entry["request"]
        if group:
            chosen = group[0]
            state = STATE_GRANTED if chosen.granted else STATE_DENIED
            approvals.append(
                Approval(
                    id=_approval_id(kind, subject),
                    kind=kind,
                    subject=subject,
                    state=state,
                    authority=authority.surface,
                    authority_store=authority.store,
                    actor=chosen.actor,
                    role=chosen.role,
                    decision=chosen.decision,
                    decision_ref=chosen.ref,
                    request_ref=request.ref if request else "",
                    requested_by=request.requested_by if request else "",
                    reason=request.reason if request else "",
                )
            )
        else:
            # A request with no decision record is PENDING — never granted. The
            # adapter cannot grant what the fleet did not grant.
            approvals.append(
                Approval(
                    id=_approval_id(kind, subject),
                    kind=kind,
                    subject=subject,
                    state=STATE_PENDING,
                    authority=authority.surface,
                    authority_store=authority.store,
                    request_ref=request.ref if request else "",
                    requested_by=request.requested_by if request else "",
                    reason=request.reason if request else "",
                )
            )
    return Projection(approvals=tuple(approvals), findings=tuple(findings))


def render(projection: Projection) -> str:
    """A canonical JSON rendering (sorted keys, stable newline) for hashing."""
    return json.dumps(projection.to_dict(), indent=2, sort_keys=True) + "\n"


def decision_index(root: Path | str) -> Dict[str, Decision]:
    """Every decision keyed by its ``ref`` — the read-back index ``verify`` uses."""
    findings: List[Finding] = []
    index: Dict[str, Decision] = {}
    for decision in load_decisions(Path(root), findings):
        index[decision.ref] = decision
    return index
