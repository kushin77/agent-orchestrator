"""Consumer SDK transports (issue #41).

The SDK talks to the platform through two small seams so every client is fully
testable offline against doubles and a deployment swaps in real HTTP:

- :class:`Transport` — one request/response round trip returning the parsed
  JSON body (used by the gateway single-dispatch and the control-plane
  client).  A real HTTP transport attaches the bearer token as the
  ``Authorization`` header.
- :class:`StreamTransport` — one streaming round trip yielding a sequence of
  parsed JSON bodies, mirroring the gateway streaming handler (issue #16:
  incremental ``{"event": {...}}`` envelopes then the terminal
  ``{"status", "result", "record"}`` envelope).  A production HTTP adapter
  relays the SSE chunks; the offline doubles yield the same sequence.

:class:`HttpTransport` is a dependency-free (stdlib ``urllib``) HTTP
implementation of :class:`Transport` used as the production default; the
streaming adapter is the documented production wiring point (an SSE relay).
No network call is ever made by the SDK tests.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, Mapping, Optional, Protocol


class Transport(Protocol):
    """One request/response round trip returning the parsed JSON body."""

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]: ...


class StreamTransport(Protocol):
    """One streaming round trip yielding parsed JSON bodies."""

    def request_stream(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Iterator[Dict[str, Any]]: ...


def _drop_none(query: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (query or {}).items() if v is not None}


class HttpTransport:
    """Dependency-free HTTP transport (stdlib ``urllib``) — the default seam.

    Attaches the bearer token when supplied, serializes JSON bodies, decodes
    JSON responses and maps HTTP/network failures to typed SDK errors.  For
    streaming, implement :class:`StreamTransport` over your SSE adapter (the
    offline doubles already speak the same chunk contract).
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        if not base_url or not base_url.startswith("http"):
            raise ValueError("base_url must be an http(s) URL")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str, query: Optional[Dict[str, Any]]) -> str:
        url = f"{self.base_url}{path}"
        if query:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode(_drop_none(query))}"
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        from .errors import ApiError, TransportError

        req_headers = dict(headers or {})
        if token:
            req_headers["Authorization"] = f"Bearer {token}"
        payload = None
        if body is not None:
            req_headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._url(path, query), data=payload, method=method, headers=req_headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:  # noqa: BLE001 - non-JSON error body
                pass
            raise ApiError(exc.code, "http_error", exc.reason) from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"transport failure: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise TransportError("response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise TransportError("response body was not a JSON object")
        return parsed
