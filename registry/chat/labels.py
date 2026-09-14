"""The declared label vocabulary for a chat turn — one vocabulary, no second.

A chat turn resolves to exactly one *outcome*, and a turn that answered
additionally carries a *grounding* label. Those two families are the label sets
the feedback loop scores, and the same outcomes are what the eval fixtures
declare as expected — so the eval harness and the feedback loop cannot drift
apart: they name the same labels.

Two further vocabularies are **consumed**, never re-declared:

* the enforcement tri-state (``block`` / ``warn`` / ``log``) comes from
  ``guardrails/policy/decision.py`` (``DecisionLevel``); ``DECISION_LEVEL``
  maps each outcome onto it rather than inventing a second severity enum.
* the FinOps model tiers (``flash`` / ``pro`` / ``auditor``) come from
  ``governance/finops/policy.json``; ``TIERS`` names them because a feedback
  event's ``tier`` must be one of them.

``scripts/check-chat-eval.sh`` cross-checks both against their homes, so a
rename in either place fails a gate instead of drifting silently.
"""

from __future__ import annotations

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

#: The FinOps tier vocabulary a feedback event's ``tier`` is drawn from.
TIERS = ("flash", "pro", "auditor")
