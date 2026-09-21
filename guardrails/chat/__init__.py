"""guardrails.chat — chat-turn guardrails: DLP egress, inbound re-validation
and retrieval-injection defense (issue #507).

The chat surface of the security pillar. A conversational turn is the moment
untrusted material meets an authority that can act, so every turn is guarded in
both directions:

* **retrieval-injection defense** — retrieved material (tickets, KB documents,
  ledger excerpts) is scanned by the existing ``guardrails/dlp`` injection
  detector *before* it can enter the grounding prefix, so a poisoned source
  cannot escalate into an instruction;
* **DLP egress** — the user prompt, the assembled grounding prefix and the
  tool-call arguments all pass the existing ``guardrails/dlp`` scrub catalog
  before anything leaves; a block rule aborts the call, a redaction proceeds
  with the value replaced and never echoed into a verdict or a finding;
* **inbound re-validation** — the model's answer is untrusted: it is filtered
  through the detector's output side, and every claim is accounted for against
  the grounding the turn actually supplied — citing a source that was never
  supplied, or quoting a source in a way the source does not support, is
  refused; a claim that cites nothing is flagged;
* **tri-state, fail-closed** — each guard answers ``BLOCK`` / ``WARN`` /
  ``LOG`` (the platform's enforcement vocabulary, consumed from
  ``guardrails/policy``), the turn's verdict is the strongest answer any guard
  gave, and a guard that could not assess the turn forces ``BLOCK`` instead of
  defaulting to allow;
* **policy binding** — the turn honours the ``guardrails/policy`` controls
  registry (default OFF), so flipping a bound control genuinely changes the
  turn's verdict for a fixed input.

This lane **consumes** ``guardrails/dlp`` (issue #27) and ``guardrails/policy``
(issue #26) read-only; neither package is edited here. The retrieved-material
envelope is coded against the small documented shape in
:mod:`guardrails.chat.envelope` (the contract the grounding lane, issue #504,
publishes), never against that package's internals, so these guards stand alone.

Import as ``chat`` with ``guardrails/`` on ``sys.path`` (the sibling packages'
convention), or as ``guardrails.chat`` with the repository root on ``sys.path``:

.. code-block:: python

    from chat import ChatTurnGuard, OutboundTurn

    guard = ChatTurnGuard()
    turn = guard.guard_turn(
        OutboundTurn(user_prompt="Summarize the incident ticket."),
        envelope={"fragments": [{"source_id": "ticket:OPS-1187", "text": "..."}]},
    )
    turn.allowed         # False once any guard refuses
    turn.dispatch_text   # the payload that may leave (redacted), or "" when refused


---knowledge---
module_id: guardrails.chat
system: guardrails
app: chat
solution_class: class
patterns: [package-contract, public-surface, fail-closed]
derives_from: null
owner_sme: security-sme
tier: L0
interfaces: [GuardOutcome, AttachedVerdicts, GroundingEnvelope, RetrievalGuard, ChatEgressGuard, InboundValidator, ChatTurnGuard, ControlBinding, GUARDS]
invariants: "a turn's verdict is the strongest answer any guard gave, and a guard that could not run is undecidable, never a pass"
gotchas: "the guard submodules are imported relatively so the package resolves under either its bare or its qualified name"
related: ["#507"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import sys
from pathlib import Path

# ``guardrails/`` has no ``__init__.py`` (mirroring ``engine/``), so the sibling
# packages this lane consumes are imported with ``guardrails/`` on sys.path —
# exactly as their own suites and ``e2e/wiring.py`` do.
_GUARDRAILS_ROOT = Path(__file__).resolve().parents[1]
if str(_GUARDRAILS_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARDRAILS_ROOT))

from .egress import (  # noqa: E402
    ChatEgressGuard,
    ComponentDecision,
    EgressOutcome,
    OutboundTurn,
)
from .envelope import (  # noqa: E402
    GroundingEnvelope,
    GroundingError,
    GroundingFragment,
)
from .inbound import (  # noqa: E402
    Claim,
    ClaimReview,
    InboundError,
    InboundOutcome,
    InboundValidator,
    ModelOutput,
)
from .policy import (  # noqa: E402
    CHAT_CONTROL_BINDINGS,
    ControlBinding,
    ControlBindingError,
    bound_ids,
    default_controls_path,
)
from .retrieval import (  # noqa: E402
    FragmentAdmission,
    RetrievalGuard,
    RetrievalOutcome,
)
from .turn import ChatTurnGuard, TurnOutcome  # noqa: E402
from .verdict import (  # noqa: E402
    AttachedVerdicts,
    DecisionLevel,
    GUARDS,
    GuardOutcome,
    aggregate,
    attach,
    decided,
    finding,
    undecidable,
)

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # verdicts
    "AttachedVerdicts",
    "DecisionLevel",
    "GUARDS",
    "GuardOutcome",
    "aggregate",
    "attach",
    "decided",
    "finding",
    "undecidable",
    # envelope (the consumed citations contract)
    "GroundingEnvelope",
    "GroundingError",
    "GroundingFragment",
    # retrieval
    "FragmentAdmission",
    "RetrievalGuard",
    "RetrievalOutcome",
    # egress
    "ChatEgressGuard",
    "ComponentDecision",
    "EgressOutcome",
    "OutboundTurn",
    # inbound
    "Claim",
    "ClaimReview",
    "InboundError",
    "InboundOutcome",
    "InboundValidator",
    "ModelOutput",
    # policy binding
    "CHAT_CONTROL_BINDINGS",
    "ControlBinding",
    "ControlBindingError",
    "bound_ids",
    "default_controls_path",
    # turn
    "ChatTurnGuard",
    "TurnOutcome",
]
