"""Guard honesty package (issue #28).

The honesty MODEL for every gate and guard in the repo: a tri-state status
(OK / NOT-OK / CANNOT-ASSESS), an anti-formality analyzer that flags checks
that cannot fail, a negative-control runner that proves a guard can fail, and
guard attestation so every verdict carries evidence rather than vibes.

Everything here is offline, lives strictly under ``guardrails/honesty/`` and
depends only on the Python standard library plus PyYAML (for the
negative-control manifest).
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
