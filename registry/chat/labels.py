"""The declared label vocabulary for a chat turn — one vocabulary, no second.

---knowledge---
module_id: registry.chat.labels
system: registry
app: chat
solution_class: pattern
patterns: [one-vocabulary-no-second, lazy-authority-read, fail-closed]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [OUTCOMES, GROUNDING, LABELS, DECISION_LEVEL, TIERS, tiers]
invariants: "an unreadable tier policy is refused by its own reader, never answered with an empty list"
gotchas: "the tier read is lazy on purpose: a gate fixture copies registry/chat alone into a scratch tree"
related: ["#509", "#1494"]
do_not_duplicate: null
---knowledge---

A chat turn resolves to exactly one *outcome*, and a turn that answered
additionally carries a *grounding* label. Those two families are the label sets
the feedback loop scores, and the same outcomes are what the eval fixtures
declare as expected — so the eval harness and the feedback loop cannot drift
apart: they name the same labels.

Two further vocabularies are **consumed**, never re-declared:

* the enforcement tri-state (``block`` / ``warn`` / ``log``) comes from
  ``guardrails/policy/decision.py`` (``DecisionLevel``); ``DECISION_LEVEL``
  maps each outcome onto it rather than inventing a second severity enum.
* the FinOps model tiers (``flash`` / ``pro`` / ``auditor``) are declared by
  ``governance/finops/policy.json`` and READ from there through the reader that
  already owns that policy (``governance/finops/chooser.py``); ``TIERS`` names
  them because a feedback event's ``tier`` must be one of them.

``scripts/check-chat-eval.sh`` cross-checks both against their homes, and
``scripts/check-tier-vocabulary.sh`` proves the tier read FOLLOWS its authority
(a mutated policy moves this list), so a rename fails a gate instead of drifting
silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The FinOps tier names are read from their declared authority (#1494). This
#: package is copied into scratch trees by a gate fixture
#: (``scripts/check-chat-eval.sh`` copies ``registry/chat`` alone), so the read is
#: LAZY — an import-time read of an authority those trees do not carry would make
#: this module unloadable there.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TIERS: tuple[str, ...] | None = None


def tiers() -> tuple[str, ...]:
    """The FinOps tier vocabulary a feedback event's ``tier`` is drawn from.

    Read from ``governance/finops/policy.json`` through its one reader, and
    cached. An unreadable policy is REFUSED by that reader, never answered with
    an empty list: an empty vocabulary would make every event's tier wrong by
    accident, which reads as a working gate.
    """
    global _TIERS
    if _TIERS is None:
        from governance.finops import chooser as finops

        _TIERS = tuple(finops.vocabulary(finops.load_policy())[0])
    return _TIERS


def __getattr__(name: str) -> object:
    """Resolve the declared name ``TIERS`` through :func:`tiers` (PEP 562).

    ``labels.TIERS`` and ``from registry.chat.labels import TIERS`` therefore get
    the one list while the read stays lazy. Any OTHER unknown name is still an
    ``AttributeError`` — a resolver that answered everything would turn a typo
    into a silent empty vocabulary.
    """
    if name == "TIERS":
        return tiers()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

#: What a turn resolved to — the outcomes the eval fixtures declare.
OUTCOMES = ("ANSWER", "NO_DATA", "REFUSAL", "BLOCK", "FLAG")

#: Whether the answer carried its sources (the ADR-0023 citations envelope).
GROUNDING = ("CITED", "UNCITED")

#: The label sets the FP/FN feedback loop scores.
LABELS = tuple(sorted(OUTCOMES + GROUNDING))

#: Each outcome's enforcement level, in the platform's ``DecisionLevel`` tokens.
DECISION_LEVEL = {
    "ANSWER": "log",
    "NO_DATA": "log",
    "REFUSAL": "warn",
    "BLOCK": "block",
    "FLAG": "block",
}
