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

The seam itself — the ``Transport`` protocol and the offline ``FixtureTransport``
— is shared with ``integrations/paperclip/`` (``integrations/_seam/``, issue
#1208), as are ``Response`` and the rendering behind ``error_for_status``. Only
the live transport is this adapter's own, because only its own wire differs: the
service is keyless, so it sends no auth header.

The client is **read-only by construction**: every verb is a ``GET`` against the
service's declared endpoints (``/health``, ``/api/capabilities``, ``/api/router``,
``/api/tiering``), and it decides no routing — it only *projects* what the
service declares. Routing authority stays with the fleet brain (ADR-0012).
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .._seam.transport import FixtureTransport as _SeamFixtureTransport
from .._seam.transport import Transport  # noqa: F401 - re-exported at this adapter's seam
from .._seam.wire import decode
from .model import BOUNDARY, Response, error_for_status

#: The declared service endpoints (mapping.SERVICE_ENDPOINTS, restated by path).
HEALTH_PATH = "/health"
CAPABILITIES_PATH = "/api/capabilities"
ROUTER_PATH = "/api/router"
TIERING_PATH = "/api/tiering"

#: The declared service port (ADR-0012 Context 7).
SERVICE_PORT = 9501

# ``Transport`` — the seam's protocol — is the shared one
# (``integrations/_seam/transport.py``, issue #1208): it is the union of what the
# two adapters ask of a transport, and it is re-exported above so existing
# callers keep importing it from here. This adapter's live transport implements
# it too; it simply never passes a body or a header.


class HttpTransport:
    """The live transport: ``{base_url}{path}`` over ``urllib.request``.

    ``path`` is one of the service's declared endpoints. Non-2xx responses are
    raised as the typed error their status maps to. This class is the only
    network path in the adapter, and it is instantiated exclusively by the
    ``probe`` verb — never by the gate or the tests.

    The client issues nothing but ``GET``s, so ``json`` and ``headers`` are
    accepted — the shared ``Transport`` protocol carries them — but are never
    sent by this adapter.
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        url = "%s%s" % (self.base_url, path)
        data: Optional[bytes] = None
        sent: Dict[str, str] = dict(headers or {})
        if json is not None:
            data = _json.dumps(json).encode("utf-8")
            sent.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=sent)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
                return Response(
                    status=int(resp.status),
                    body=decode(raw),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = str(decode(exc.read().decode("utf-8")))
            except Exception:  # pragma: no cover - best-effort body read
                detail = ""
            raise error_for_status(int(exc.code), path, detail) from exc


class FixtureTransport(_SeamFixtureTransport):
    """The offline transport: the shared seam's, over this adapter's boundary.

    A fixture is a mapping ``{"responses": [ {"method", "path", "status",
    "body"} ... ]}``. Every call is recorded on ``requests`` (method, path,
    headers) so the tests can assert the request *shape* — the declared
    endpoints, nothing else — without a network. The service is keyless and its
    ``/health`` is root-level, so the seam is configured here with no path prefix
    and no auth headers. An unmatched request raises ``KeyError`` (a loud miss,
    never a silent default), so a drifted client path fails the tests.
    """

    def __init__(self, fixture: Dict[str, Any]) -> None:
        super().__init__(fixture, boundary=BOUNDARY)


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
