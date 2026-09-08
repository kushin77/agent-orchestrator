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
