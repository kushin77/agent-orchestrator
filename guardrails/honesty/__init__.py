"""Guard honesty package (issue #28).

The honesty MODEL for every gate and guard in the repo: a tri-state status
(OK / NOT-OK / CANNOT-ASSESS), an anti-formality analyzer that flags checks
that cannot fail, a negative-control runner that proves a guard can fail, and
guard attestation so every verdict carries evidence rather than vibes.

Everything here is offline, lives strictly under ``guardrails/honesty/`` and
depends only on the Python standard library plus PyYAML (for the
negative-control manifest).


---knowledge---
module_id: guardrails.honesty
system: guardrails
app: honesty
solution_class: class
patterns: [package-contract, public-surface, tri-state-exit]
derives_from: null
owner_sme: qa-sme
tier: L0
interfaces: [TriState, GuardAttestation, aggregate, from_exit_code, parse, serialize, to_exit_code]
invariants: "one tri-state model for every gate and guard in the repo"
gotchas: "it depends only on the Python standard library plus PyYAML for the negative-control manifest"
related: ["#28"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .attestation import GuardAttestation
from .tristate import (
    TriState,
    aggregate,
    from_exit_code,
    parse,
    serialize,
    to_exit_code,
)

__all__ = [
    "TriState",
    "GuardAttestation",
    "aggregate",
    "from_exit_code",
    "parse",
    "serialize",
    "to_exit_code",
]

__version__ = "0.1.0"
