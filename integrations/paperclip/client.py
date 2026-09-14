"""The upstream paperclip.ing HTTP client, behind a transport seam (issue #428).

ADR-0013 integrates the operator surface over its HTTP API across a **process
boundary**. That boundary is expressed here as a single seam — the ``Transport``
protocol — with two implementations:

* ``HttpTransport`` — the real one, built on the standard library
  (``urllib.request``) only, so the adapter needs no third-party dependency;
* ``FixtureTransport`` — the offline one, replaying canned responses from a
  fixture file. Both the tests and the ``make verify`` gate use it, which is why
  **the gate never touches the network**.

Auth and run correlation are applied at the seam, uniformly: every request
carries ``Authorization: Bearer <token>``, and a mutating request (``POST`` /
``PATCH`` / ``PUT`` / ``DELETE``) made during a run carries
``X-Paperclip-Run-Id``. Upstream status codes map to the typed errors in
``model.py``.
"""

from __future__ import annotations

import json as _json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .model import Response, error_for_status

#: HTTP methods that mutate upstream state and therefore carry the run header.
MUTATING_METHODS = ("POST", "PATCH", "PUT", "DELETE")

#: The upstream API path prefix. Every request path starts here.
API_PREFIX = "/api"


def build_headers(*, token: str, run_id: Optional[str], method: str) -> Dict[str, str]:
    """The auth + run-correlation headers for one request.

    Shared by both transports so the offline fixture path exercises exactly the
    headers the live path sends — the gate asserts on this, not on a copy.
    """
    headers: Dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if run_id and method.upper() in MUTATING_METHODS:
        headers["X-Paperclip-Run-Id"] = run_id
    return headers


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


class HttpTransport:
    """The live transport: ``{base_url}{path}`` over ``urllib.request``.

    ``path`` is always an absolute API path (``/api/...``); ``base_url`` is the
    server root (e.g. ``http://localhost:3100``). Non-2xx responses are raised as
    the typed error their status maps to.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        run_id: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.run_id = run_id
        self.timeout = timeout

    def url_for(self, path: str) -> str:
        if not path.startswith(API_PREFIX):
            raise ValueError(f"path must start with {API_PREFIX!r}: {path!r}")
        return f"{self.base_url}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        url = self.url_for(path)
        merged = build_headers(token=self.token, run_id=self.run_id, method=method)
        data: Optional[bytes] = None
        if json is not None:
            data = _json.dumps(json).encode("utf-8")
            merged["Content-Type"] = "application/json"
        if headers:
            merged.update(headers)
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=merged)
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
                detail = _decode(exc.read().decode("utf-8")).get("error", "")
            except Exception:  # pragma: no cover - best-effort body read
                detail = ""
            raise error_for_status(int(exc.code), path, str(detail)) from exc


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

    A fixture is either a mapping ``{"responses": [ {"method", "path", "status",
    "body"} ... ]}`` or the path to a JSON file holding one. Every call is
    recorded on ``requests`` (method, path, headers) so the gate can assert the
    request *shape* — prefix, company scoping, auth and run header — without a
    network. An unmatched request raises ``KeyError`` (a loud miss, never a
    silent default), so a drifted client path fails the gate.
    """

    def __init__(
        self,
        fixture: Any,
        *,
        token: str = "",
        run_id: Optional[str] = None,
    ) -> None:
        if isinstance(fixture, (str, bytes, os.PathLike)):
            with open(fixture, encoding="utf-8") as fh:
                fixture = _json.load(fh)
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
        self.token = token
        self.run_id = run_id
        self.requests: List[Dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        if not path.startswith(API_PREFIX):
            raise ValueError(f"path must start with {API_PREFIX!r}: {path!r}")
        merged = build_headers(token=self.token, run_id=self.run_id, method=method)
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
            raise error_for_status(response.status, path)
        return response


class PaperclipClient:
    """Typed calls over the upstream ``/api`` surface (company-scoped).

    ``company_id`` scopes the resource families under
    ``/api/companies/{companyId}/...``; ``health`` and ``openapi`` are unscoped.
    """

    def __init__(
        self,
        base_url: str = "",
        *,
        token: str = "",
        company_id: str = "",
        run_id: Optional[str] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        self.company_id = company_id
        self.run_id = run_id
        if transport is not None:
            self.transport: Transport = transport
        else:
            self.transport = HttpTransport(base_url, token=token, run_id=run_id)

    # -- unscoped ----------------------------------------------------------
    def health(self) -> Response:
        return self.transport.request("GET", f"{API_PREFIX}/health")

    def openapi(self) -> Response:
        return self.transport.request("GET", f"{API_PREFIX}/openapi.json")

    # -- company-scoped ----------------------------------------------------
    def _company_path(self, resource: str) -> str:
        if not self.company_id:
            raise ValueError("company_id is required for company-scoped calls")
        return f"{API_PREFIX}/companies/{self.company_id}/{resource}"

    def agents(self) -> Response:
        return self.transport.request("GET", self._company_path("agents"))

    def issues(self) -> Response:
        return self.transport.request("GET", self._company_path("issues"))

    def costs(self) -> Response:
        return self.transport.request("GET", self._company_path("costs"))

    def approvals(self) -> Response:
        return self.transport.request("GET", self._company_path("approvals"))

    def activity(self) -> Response:
        return self.transport.request("GET", self._company_path("activity"))

    def dashboard(self) -> Response:
        return self.transport.request("GET", self._company_path("dashboard"))

    # -- mutating (carry X-Paperclip-Run-Id through the seam) --------------
    def create_issue(self, payload: Dict[str, Any]) -> Response:
        return self.transport.request("POST", self._company_path("issues"), json=payload)

    def update_issue(self, issue_id: str, payload: Dict[str, Any]) -> Response:
        return self.transport.request(
            "PATCH", f"{API_PREFIX}/issues/{issue_id}", json=payload
        )

    def request_topup(self, payload: Dict[str, Any]) -> Response:
        return self.transport.request(
            "POST", self._company_path("approvals"), json=payload
        )
