"""The transport seam both adapters share: the ``Transport`` Protocol and the offline ``FixtureTransport`` (#1208).

The Protocol is the union of what the two adapters ask of a transport: paperclip
sends a JSON body and per-request headers (its run correlation), hermes sends
neither. A transport that ignores the two keyword arguments is therefore a valid
implementation, and so is one that carries them — which is why the wider
signature is the shared one (each adapter's own live ``HttpTransport`` keeps its
own behaviour: paperclip's adds ``Authorization: Bearer`` and
``X-Paperclip-Run-Id`` and owns the ``/api`` prefix, hermes's is keyless).

``FixtureTransport`` was one class written twice. The two copies differed in
exactly two ways, and both are configuration rather than logic, so both are
constructor arguments here:

* ``boundary`` — the adapter's wire vocabulary, so a non-2xx raises *its* typed
  error (the fixture transport is the offline path, and the gate asserts on the
  error it raises);
* ``prefix`` — when set, a request path outside it is refused: paperclip's
  ``/api`` contract. The default is no prefix constraint, which is what the
  keyless hermes service needs (its ``/health`` is root-level);
* ``headers_for(method)`` — the headers recorded per request, or ``None`` to
  record none. Paperclip's copy built its auth + run headers here so the gate's
  request-shape assertions exercise the same headers the live path sends.
"""

from __future__ import annotations

import json as _json
import os
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from .wire import Response, WireBoundary


@runtime_checkable
class Transport(Protocol):
    """The transport seam: one request, one response, no other coupling."""

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:  # pragma: no cover - protocol declaration
        ...


class FixtureTransport:
    """The offline transport: replays canned responses from a fixture.

    A fixture is either a mapping ``{"responses": [ {"method", "path", "status",
    "body"} ... ]}`` or the path to a JSON file holding one. Every call is
    recorded on ``requests`` (method, path, headers) so a gate can assert the
    request *shape* — prefix, scoping, auth and run header — without a network.
    An unmatched request raises ``KeyError`` (a loud miss, never a silent
    default), so a drifted client path fails the gate.
    """

    def __init__(
        self,
        fixture: Any,
        *,
        boundary: WireBoundary,
        prefix: str = "",
        headers_for: Optional[Callable[[str], Dict[str, str]]] = None,
    ) -> None:
        if isinstance(fixture, (str, bytes, os.PathLike)):
            with open(fixture, encoding="utf-8") as fh:
                fixture = _json.load(fh)
        responses = fixture.get("responses") if isinstance(fixture, dict) else None
        if not isinstance(responses, list):
            raise ValueError("fixture must be an object with a 'responses' list")
        self.boundary = boundary
        self.prefix = prefix
        self._headers_for = headers_for
        self._table: Dict[tuple, Response] = {}
        for entry in responses:
            key = (str(entry["method"]).upper(), str(entry["path"]))
            self._table[key] = Response(
                status=int(entry.get("status", 200)),
                body=entry.get("body"),
                headers={k.lower(): v for k, v in (entry.get("headers") or {}).items()},
            )
        self.requests: List[Dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        if self.prefix and not path.startswith(self.prefix):
            raise ValueError(f"path must start with {self.prefix!r}: {path!r}")
        merged: Dict[str, str] = dict(self._headers_for(method)) if self._headers_for else {}
        if json is not None:
            merged["Content-Type"] = "application/json"
        if headers:
            merged.update(headers)
        self.requests.append({"method": method.upper(), "path": path, "headers": merged})
        try:
            response = self._table[(method.upper(), path)]
        except KeyError as exc:
            raise KeyError(f"no fixture response for {method.upper()} {path}") from exc
        if not response.ok:
            raise self.boundary.error_for_status(response.status, path)
        return response
