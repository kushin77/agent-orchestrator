"""Approvals as a projected resource (issue #416, EPIC #410).

Upstream paperclip.ing models an *approval* as a first-class object — approve a
hire, approve a budget top-up, approve/override a strategy — carrying an actor
and an audit trail. The fleet already holds the **authority** for each of those
(``.fleet/sent`` directives, ``governance/dispatch`` claims, the control verb);
this package publishes an object an operator or an API consumer can act on.

The EPIC's rule is decisive: an approval is a **projection of an existing
authority, never a second one**. This package therefore has no store, no write
path, and no default grant — every ``granted`` state is read back from, and
re-verified against, the authoritative record it names. See ``README.md`` for
the kind -> authority mapping and the refused cases.

---knowledge---
module_id: integrations.paperclip.adapters.approvals.__init__
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from . import record  # noqa: F401  (issue #1272: the signed-record authority; see record.py)
from .model import (
    AUTHORITIES,
    KIND_HIRE,
    KIND_OVERRIDE,
    KIND_TOP_UP,
    KINDS,
    STATE_DENIED,
    STATE_GRANTED,
    STATE_PENDING,
    STATES,
    Approval,
    ApprovalRefused,
    Authority,
    Decision,
    Finding,
    Request,
)

__all__ = [
    "record",
    "AUTHORITIES",
    "KIND_HIRE",
    "KIND_OVERRIDE",
    "KIND_TOP_UP",
    "KINDS",
    "STATE_DENIED",
    "STATE_GRANTED",
    "STATE_PENDING",
    "STATES",
    "Approval",
    "ApprovalRefused",
    "Authority",
    "Decision",
    "Finding",
    "Request",
]
