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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class Response:
    """One upstream HTTP response, parsed at the transport seam.

    ``body`` is the decoded JSON body when the response is JSON, else the raw
    text. ``status`` is the HTTP status code, always present.
    """

    status: int
    body: Any = None
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


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


def error_for_status(status: int, path: str, detail: str = "") -> HermesError:
    """Build the typed error a given upstream status maps to."""
    cls = ERROR_BY_STATUS.get(status, HermesError)
    message = "hermes %s: HTTP %s" % (path, status)
    if detail:
        message = "%s — %s" % (message, detail)
    return cls(message, status=status, path=path)


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
