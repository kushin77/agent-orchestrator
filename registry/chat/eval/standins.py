"""Deterministic stand-ins for the collaborators a chat turn depends on.

---knowledge---
module_id: registry.chat.eval.standins
system: registry
app: eval
solution_class: pattern
patterns: [stand-in-collaborators, offline-deterministic]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [Response, respond, retrieve, admitted, supplied_source_ids, case_fragments]
invariants: "every stand-in is a pure function of the fixture, and restates a sibling lane's contract in miniature"
gotchas: ""
related: ["#509"]
do_not_duplicate: null
---knowledge---

The eval harness is offline and deterministic: it may not call a model, a
retrieval service, the guardrails lane or the network. What it needs from each
collaborator is therefore *declared here as a fixture-driven stand-in*, and the
expectations the harness asserts are the sibling lanes' contracts in miniature:

* the **model** stand-in (:func:`respond`) is a pure function of the resolved
  module's declared ``groundingPolicy`` and the case's fixture context. It reads
  no clock, no random source and no network, so a case always yields the same
  document and a regression is always reproducible.
* the **grounding** stand-in (:func:`tenant_scope`, :func:`retrieve`,
  :func:`admitted`) reads the case's fragment fixtures — supplied, foreign-tenant
  or quarantined.
* the **guard** stand-ins (:func:`inbound` plus the two above) are the refusals
  the chat guard lane (issue #507) owns, expressed as the *declared expected
  outcomes* of the fixture: a request carrying a credential is blocked before
  dispatch, a quarantined retrieved document is flagged and never reaches the
  prompt prefix.
* **cross-tenant** refusal is the identity lane's contract (``identity/chat``),
  expressed here as a declared outcome rather than an import.

None of this is production code, and the module says so: replacing a stand-in
with the real component is the integration step, and the eval cases then assert
the same declared outcomes against the real one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .. import envelope
from ..prompt_modules import ChatPromptRegistry, ResolvedChatModule

#: The case kinds the committed fixture set declares (one per quality risk).
CASE_KINDS = (
    "grounded",
    "unanswerable",
    "cross-tenant",
    "secret-inbound",
    "poisoned-document",
)

#: The module that answers each outcome family.
ANSWER_TASK_TYPE = "chat-answer"
REFUSAL_TASK_TYPE = "chat-refuse"

#: Reason codes — machine-readable, declared in the fixtures as expected too.
REASON_CODES = {
    "NO_DATA_NO_SOURCE": "no supplied fragment answers the question",
    "REFUSAL_CROSS_TENANT": "a supplied fragment belongs to another tenant",
    "BLOCK_INBOUND_CREDENTIAL": "the request carried a credential",
    "FLAG_POISONED_SOURCE": "a supplied source was quarantined",
}

#: How an outcome is spelled in a refusal document (the refusal schema's enum).
DOCUMENT_OUTCOME = {
    "NO_DATA": "no-data",
    "REFUSAL": "refusal",
    "BLOCK": "block",
    "FLAG": "flag",
}


def inbound(question: str, canary: Optional[str]) -> Optional[str]:
    """The inbound guard's stand-in: refuse a request carrying the fixture's canary.

    A real guard scans for credential *shapes*; the fixture declares a synthetic
    canary value instead, so the eval tree carries no credential-shaped string
    and the case is still a genuine detection (the prompt must actually carry
    the value for the turn to be blocked).
    """
    if canary and canary in question:
        return "BLOCK_INBOUND_CREDENTIAL"
    return None


def tenant_scope(fragments: Sequence[Mapping[str, Any]], tenant: str) -> Optional[str]:
    """Refuse a turn whose retrieved set contains another tenant's fragment."""
    for fragment in fragments:
        owner = fragment.get("tenant")
        if owner is not None and str(owner) != str(tenant):
            return "REFUSAL_CROSS_TENANT"
    return None


def retrieve(fragments: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """Flag a turn whose retrieved set contains a quarantined document."""
    for fragment in fragments:
        if bool(fragment.get("poisoned", False)):
            return "FLAG_POISONED_SOURCE"
    return None


def admitted(fragments: Sequence[Mapping[str, Any]]) -> Tuple[Mapping[str, Any], ...]:
    """The fragments that survive admission, in fixture order."""
    return tuple(
        fragment for fragment in fragments if not bool(fragment.get("poisoned", False))
    )


def supplied_source_ids(case: Mapping[str, Any]) -> Tuple[str, ...]:
    """Every ``source_id`` the turn was supplied, in fixture order."""
    return tuple(str(fragment.get("source_id")) for fragment in case_fragments(case))


def case_fragments(case: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    """The case's retrieved fragments (empty when the fixture supplies none)."""
    context = case.get("context") or []
    return tuple(context)


@dataclass(frozen=True)
class Response:
    """What the stand-ins produced for one case."""

    outcome: str
    reason_code: Optional[str]
    reason: str
    document: Mapping[str, Any]
    module: ResolvedChatModule
    citations: Tuple[envelope.Citation, ...]
    admitted: Tuple[Mapping[str, Any], ...]

    @property
    def cited_source_ids(self) -> Tuple[str, ...]:
        return tuple(entry.source_id for entry in self.citations)


def _refusal(outcome: str, reason_code: str, module: ResolvedChatModule) -> Response:
    document = {
        "outcome": DOCUMENT_OUTCOME[outcome],
        "reason": REASON_CODES[reason_code],
        "reason_code": reason_code,
        "citations": [],
    }
    return Response(
        outcome=outcome,
        reason_code=reason_code,
        reason=REASON_CODES[reason_code],
        document=document,
        module=module,
        citations=(),
        admitted=(),
    )


def _answer(
    fragments: Sequence[Mapping[str, Any]],
    module: ResolvedChatModule,
    *,
    cite: bool,
) -> Response:
    citations = envelope.envelope(fragments) if cite else ()
    document: Dict[str, Any] = {
        "answer": " ".join(str(fragment.get("text", "")) for fragment in fragments),
        "citations": [entry.to_dict() for entry in citations],
    }
    return Response(
        outcome="ANSWER",
        reason_code=None,
        reason="",
        document=document,
        module=module,
        citations=citations,
        admitted=tuple(fragments),
    )


def respond(
    case: Mapping[str, Any],
    registry: ChatPromptRegistry,
    versions: Optional[Mapping[str, str]] = None,
) -> Response:
    """Produce the response for one case, deterministically.

    The module that answers is *selected by the outcome*, not taken from the
    case: an answering turn resolves the answering module, a refusal resolves
    the refusal module. The harness then compares that selection against the
    module the case declares — which is what makes "the right module answered"
    a check rather than a restatement of the fixture.
    """
    overrides = dict(versions or {})
    fragments = case_fragments(case)
    canary = case.get("canary")
    reason_code = inbound(str(case.get("question", "")), canary)
    if reason_code is None:
        reason_code = tenant_scope(fragments, str(case.get("tenant", "")))
    if reason_code is None:
        reason_code = retrieve(fragments)

    if reason_code is not None:
        task_type = REFUSAL_TASK_TYPE
        module = registry.resolve(task_type, overrides.get(task_type))
        return _refusal(_outcome_for(reason_code), reason_code, module)

    keep = admitted(fragments)
    if not keep:
        task_type = REFUSAL_TASK_TYPE
        module = registry.resolve(task_type, overrides.get(task_type))
        return _refusal("NO_DATA", "NO_DATA_NO_SOURCE", module)

    task_type = ANSWER_TASK_TYPE
    module = registry.resolve(task_type, overrides.get(task_type))
    policy = module.grounding_policy
    if policy == "refuse-without-citations":
        return _refusal("NO_DATA", "NO_DATA_NO_SOURCE", module)
    return _answer(keep, module, cite=policy == "require-citations")


def _outcome_for(reason_code: str) -> str:
    return {
        "REFUSAL_CROSS_TENANT": "REFUSAL",
        "BLOCK_INBOUND_CREDENTIAL": "BLOCK",
        "FLAG_POISONED_SOURCE": "FLAG",
        "NO_DATA_NO_SOURCE": "NO_DATA",
    }[reason_code]
