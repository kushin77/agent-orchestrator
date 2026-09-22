"""Typed shapes for the hermes integration adapter (issue #942).

Two families of shapes live here, deliberately kept apart:

* **our-side source shapes** — ``Persona`` (a ``registry/personas/cards`` card)
  and ``Profile`` (a ``registry/profiles/seeds`` seed): the two declared views
  of the hermes worker the mapper reads;
* **the service contract constants** — the routing-service endpoints and the
  namesake boundary, stated once so the mapper, the CLI and the gate cannot
  drift apart (ADR-0012, Context 5 and 7).

Stdlib-only by construction (frozen dataclasses + typing), so neither the tests
nor the gate pull a third-party dependency in.

---knowledge---
module_id: integrations.hermes.model
system: integrations
app: hermes
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: hermes
tier: L1
interfaces: [HermesError, UnavailableError, error_for_status, Persona, Profile]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .._seam.wire import Response  # noqa: F401 - re-exported at this adapter's seam
from .._seam.wire import WireBoundary

# --------------------------------------------------------------------------
# Transport primitives
# --------------------------------------------------------------------------
# ``Response`` and the rendering of ``error_for_status`` are the shared seam's
# (``integrations/_seam/wire.py``, issue #1208): both were byte-identical in this
# adapter and in ``integrations/paperclip/``, so they live there now and are
# re-exported here — ``from integrations.hermes.model import Response`` keeps
# working. What stays local is this adapter's own vocabulary: the error classes
# below and the boundary that binds them to the seam's rendering.


class HermesError(Exception):
    """Base error for the hermes integration adapter."""

    #: HTTP status this error is mapped from.
    status = 0

    def __init__(self, message: str, *, status: int = 0, path: str = "") -> None:
        super().__init__(message)
        self.path = path
        self.status = status


class UnavailableError(HermesError):
    """``503`` — the routing service is unreachable (it is not deployed)."""

    status = 503


#: The documented status -> typed-error mapping.
ERROR_BY_STATUS: Dict[int, type] = {
    503: UnavailableError,
}

#: This adapter's wire vocabulary, bound to its own typed errors: the shared
#: seam renders the message and constructs the error (``_seam/wire.py``).
BOUNDARY = WireBoundary(label="hermes", error_by_status=ERROR_BY_STATUS, base_error=HermesError)


def error_for_status(status: int, path: str, detail: str = "") -> HermesError:
    """Build the typed error a given upstream status maps to.

    The status→class table and the ``hermes`` label are this adapter's; the
    message shape and the constructor call are the shared seam's.
    """
    return BOUNDARY.error_for_status(status, path, detail)


@dataclass(frozen=True)
class Persona:
    """A ``registry/personas/cards/hermes.yaml`` PersonaCard (capability view)."""

    id: str
    name: str
    posture: str
    tier: str
    lanes: tuple
    capabilities: tuple


@dataclass(frozen=True)
class Profile:
    """A ``registry/profiles/seeds/hermes.1.0.0.yaml`` AgentProfile seed."""

    id: str
    version: str
    owner: str
    default_model_tier: str
    capabilities: tuple = ()
    tools: tuple = ()
    constraints: tuple = ()
