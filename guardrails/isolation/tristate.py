"""Tri-state re-export seam for guardrails/isolation (issue #30).

The honesty tri-state model (OK / NOT-OK / CANNOT-ASSESS) is the codified
fleet doctrine and lives in the sibling ``guardrails/honesty`` lane
(issue #28, merged on ``master``).  This lane **consumes** that model
read-only rather than redefining it, so the exit-code contract and the
fail-closed aggregation rules have exactly one source of truth.

Import path note: ``guardrails/`` is a PEP-420 namespace package.  When the
isolation CLI runs from the repo root or the lane directory the package is
importable as ``honesty``; the conftest used by the pytest suite inserts
``guardrails/`` on ``sys.path`` the same way the sibling lanes do.


---knowledge---
module_id: guardrails.isolation.tristate
system: guardrails
app: isolation
solution_class: class
patterns: [consume-never-restate, tri-state-exit, package-contract]
derives_from: guardrails/honesty/tristate.py
owner_sme: security-sme
tier: L0
interfaces: [EXIT_OK, EXIT_NOT_OK, EXIT_CANNOT_ASSESS, TriState, parse_status, from_exit_code, to_exit_code, aggregate_statuses]
invariants: "the tri-state model is consumed read-only from the sibling honesty lane, so the exit-code contract has one source of truth"
gotchas: "guardrails/ is a PEP-420 namespace package, so the sibling lane is importable under its bare name"
related: ["#30", "#28"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from honesty.tristate import (  # noqa: F401  (re-exported for this lane)
    EXIT_CANNOT_ASSESS,
    EXIT_NOT_OK,
    EXIT_OK,
    TriState,
    aggregate as aggregate_statuses,
    from_exit_code,
    parse as parse_status,
    to_exit_code,
)

__all__ = [
    "EXIT_CANNOT_ASSESS",
    "EXIT_NOT_OK",
    "EXIT_OK",
    "TriState",
    "aggregate_statuses",
    "from_exit_code",
    "parse_status",
    "to_exit_code",
]
