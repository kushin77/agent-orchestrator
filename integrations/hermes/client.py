"""The hermes-agents service client, behind a transport seam (issue #942).

ADR-0012 integrates the routing-service contract across a **process boundary**
that is, measured, ``deployable-not-running``: the service is never called at
dispatch time and this repo never depends on it. That boundary is expressed
here as a single seam — the ``Transport`` protocol — with two implementations:

* ``HttpTransport`` — the live one, built on the standard library
  (``urllib.request``) only, so the adapter needs no third-party dependency;
* ``FixtureTransport`` — the offline one, replaying canned responses from a
  fixture mapping. Both the tests and the ``make verify`` gate use it, which is
  why **the gate never touches the network**.

The client is **read-only by construction**: every verb is a ``GET`` against the
service's declared endpoints (``/health``, ``/api/capabilities``, ``/api/router``,
``/api/tiering``), and it decides no routing — it only *projects* what the
service declares. Routing authority stays with the fleet brain (ADR-0012).
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .model import Response, error_for_status

#: The declared service endpoints (mapping.SERVICE_ENDPOINTS, restated by path).
HEALTH_PATH = "/health"
CAPABILITIES_PATH = "/api/capabilities"
ROUTER_PATH = "/api/router"
TIERING_PATH = "/api/tiering"

#: The declared service port (ADR-0012 Context 7).
SERVICE_PORT = 9501


@runtime_checkable
class Transport(Protocol):
    """The transport seam: one request, one response, no other coupling."""

    def request(self, method: str, path: str) -> Response:  # pragma: no cover - protocol
        ...


class HttpTransport:
    """The live transport: ``{base_url}{path}`` over ``urllib.request``.

    ``path`` is one of the service's declared endpoints. Non-2xx responses are
    raised as the typed error their status maps to. This class is the only
    network path in the adapter, and it is instantiated exclusively by the
    ``probe`` verb — never by the gate or the tests.
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str) -> Response:
        url = "%s%s" % (self.base_url, path)
        req = urllib.request.Request(url, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
                return Response(
                    status=int(resp.status),
                    body=_decode(raw),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = str(_decode(exc.read().decode("utf-8")))
            except Exception:  # pragma: no cover - best-effort body read
                detail = ""
            raise error_for_status(int(exc.code), path, detail) from exc


def _decode(raw: str) -> Any:
    """Decode a body as JSON, falling back to the raw text."""
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except ValueError:
        return raw


class FixtureTransport:
    """The offline transport: replays canned responses from a fixture.

    A fixture is a mapping ``{"responses": [ {"method", "path", "status",
    "body"} ... ]}``. Every call is recorded on ``requests`` (method, path) so
    the tests can assert the request *shape* — the declared endpoints, nothing
    else — without a network. An unmatched request raises ``KeyError`` (a loud
    miss, never a silent default), so a drifted client path fails the tests.
    """

    def __init__(self, fixture: Dict[str, Any]) -> None:
        responses = fixture.get("responses") if isinstance(fixture, dict) else None
        if not isinstance(responses, list):
            raise ValueError("fixture must be an object with a 'responses' list")
        self._table: Dict[tuple, Response] = {}
        for entry in responses:
            key = (str(entry["method"]).upper(), str(entry["path"]))
            self._table[key] = Response(
                status=int(entry.get("status", 200)),
                body=entry.get("body"),
                headers={k.lower(): v for k, v in (entry.get("headers") or {}).items()},
            )
        self.requests: List[Dict[str, str]] = []

    def request(self, method: str, path: str) -> Response:
        self.requests.append({"method": method.upper(), "path": path})
        try:
            response = self._table[(method.upper(), path)]
        except KeyError as exc:
            raise KeyError("no fixture response for %s %s" % (method.upper(), path)) from exc
        if not response.ok:
            raise error_for_status(response.status, path)
        return response


class HermesClient:
    """Read-only projection calls over the service's declared endpoints.

    The four verbs map one-to-one onto the service contract; none of them
    routes work, and none of them needs an API key (the service is keyless,
    ``gateway/catalog/modules/hermes/module.json``).
    """

    def __init__(self, base_url: str = "", *, transport: Optional[Transport] = None) -> None:
        if transport is not None:
            self.transport: Transport = transport
        else:
            self.transport = HttpTransport(base_url)

    def health(self) -> Response:
        return self.transport.request("GET", HEALTH_PATH)

    def capabilities(self) -> Response:
        return self.transport.request("GET", CAPABILITIES_PATH)

    def router(self) -> Response:
        return self.transport.request("GET", ROUTER_PATH)

    def tiering(self) -> Response:
        return self.transport.request("GET", TIERING_PATH)
