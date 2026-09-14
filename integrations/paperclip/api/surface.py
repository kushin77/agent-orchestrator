"""The route surface, READ from the adapter's own client (issue #413).

The OpenAPI document must not describe a surface the adapter does not actually
call — a second, hand-written route list is exactly the parallel description the
lane forbids. So the route set is **introspected from
``integrations/paperclip/client.py``**: each public, argument-free method of
:class:`~integrations.paperclip.client.PaperclipClient` is invoked against a
recording transport, and the ``(method, path)`` it emits is read back. The
company placeholder ``{companyId}`` is what the client interpolates, so the
templates it produces are the document's own path templates.

A method that needs arguments (a mutation) is skipped: this is the *read*
surface. The result is deterministic — sorted by ``(method, path)``.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Dict, List, Tuple

from ..client import API_PREFIX, PaperclipClient
from ..model import Response

#: The company-scoped path template the upstream route shape uses.
COMPANY_PATH = API_PREFIX + "/companies/{companyId}"

#: The placeholder the client interpolates when its routes are read.
COMPANY_PLACEHOLDER = "{companyId}"


@dataclass(frozen=True)
class Route:
    """One read route the adapter's client emits."""

    method: str
    path: str
    operation_id: str

    @property
    def company_scoped(self) -> bool:
        return self.path.startswith(COMPANY_PATH + "/")

    @property
    def segment(self) -> str:
        """The resource segment of a company-scoped route (``""`` otherwise)."""
        if not self.company_scoped:
            return ""
        return self.path[len(COMPANY_PATH) + 1 :]


class _RecordingTransport:
    """A transport that records the ``(method, path)`` of every request."""

    def __init__(self) -> None:
        self.requests: List[Tuple[str, str]] = []

    def request(self, method: str, path: str, *, json: object = None, headers: object = None) -> Response:
        self.requests.append((str(method).upper(), str(path)))
        return Response(status=200, body={})


def client_routes() -> Tuple[Route, ...]:
    """Every argument-free read route the adapter's client emits, sorted."""
    transport = _RecordingTransport()
    client = PaperclipClient(company_id=COMPANY_PLACEHOLDER, transport=transport)
    found: Dict[Tuple[str, str], str] = {}

    for name, _member in sorted(inspect.getmembers(PaperclipClient, inspect.isfunction)):
        if name.startswith("_"):
            continue
        bound = getattr(client, name)
        try:
            signature = inspect.signature(bound)
        except (TypeError, ValueError):
            continue
        required = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.default is parameter.empty
            and parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if required:
            continue  # a mutation (or a helper): not part of the read surface
        before = len(transport.requests)
        try:
            bound()
        except Exception:  # a method that cannot be exercised is not a route
            continue
        for method, path in transport.requests[before:]:
            found.setdefault((method, path), name)

    return tuple(Route(method, path, found[(method, path)]) for method, path in sorted(found))


def unscoped_routes() -> Tuple[Route, ...]:
    """The read routes that are not company-scoped (``/api/health``, ``/api/openapi.json``)."""
    return tuple(route for route in client_routes() if not route.company_scoped)


def company_routes() -> Tuple[Route, ...]:
    """The company-scoped read routes."""
    return tuple(route for route in client_routes() if route.company_scoped)
