"""Pluggable HTTP transport for provider adapters (issue #15).

Adapters never open sockets themselves. They call an injected
``HttpTransport`` (``request(...)``), so every provider test runs fully
offline against a recording/failing transport and the same adapter code talks
to the real provider through ``StdlibHttpTransport`` in production (stdlib
``urllib`` only - no third-party HTTP client is required).

- ``HttpResponse`` - the transport-neutral response value.
- ``StdlibHttpTransport`` - real HTTP over stdlib (never exercised by the
  offline test suite).
- ``RecordingTransport`` - scripted offline double: returns canned responses
  (or raises canned errors) and records every request so tests can assert on
  method/url/headers/body (e.g. that the API key header is present but that
  the key is never logged anywhere).
- ``FailingTransport`` - raises ``ProviderUnavailableError`` on every request
  (used for retry/circuit-breaker negative tests).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from providers.errors import (
    ProviderTimeoutError,
    ProviderUnavailableError,
)


@dataclass(frozen=True)
class HttpResponse:
    """Transport-neutral HTTP response (headers normalized to lower-case)."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str = ""


class HttpTransport(Protocol):
    """A transport that performs one HTTP request (injected into adapters)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        """Perform one HTTP request and return the response.

        Raises ProviderUnavailableError for transient failures (network,
        5xx) and ProviderTimeoutError when the timeout elapses. 4xx-class
        failures are returned as non-2xx ``HttpResponse`` (the adapter maps
        them to a non-transient ProviderError).
        """
        ...


class StdlibHttpTransport:
    """Real HTTP transport using the standard library (no extra dependency).

    Timeouts raise ``ProviderTimeoutError``; network failures and 5xx raise
    ``ProviderUnavailableError`` (transient - safe to retry); 4xx responses
    are returned to the adapter, which maps them to a non-transient error.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        import urllib.error
        import urllib.request

        payload = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method=method.upper())
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        timeout_s = (timeout_ms if timeout_ms is not None else 30000) / 1000.0
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # nosec
                raw = resp.read().decode("utf-8", "replace")
                normalized = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(status=resp.status, headers=normalized, body=raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            if exc.code >= 500:
                raise ProviderUnavailableError(
                    f"provider returned HTTP {exc.code}", provider=None
                ) from exc
            return HttpResponse(status=exc.code, headers={}, body=raw)
        except TimeoutError as exc:
            raise ProviderTimeoutError("request timed out") from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(f"transport error: {exc.reason}") from exc


class RecordingTransport:
    """Offline scripted transport: canned responses/errors + full request log.

    ``script`` is a list of items consumed in order; an item is either an
    ``HttpResponse`` (returned), an ``Exception`` (raised), or a callable
    returning one of those. When the script is exhausted ``exhausted``
    controls the behaviour (default: raise a transient error so a test that
    under-scripts fails loudly rather than passing silently).
    """

    def __init__(
        self,
        script: list[Any] | None = None,
        *,
        exhausted: str = "raise",
    ) -> None:
        self._script = list(script or [])
        self._exhausted = exhausted
        self.requests: list[dict[str, Any]] = []

    def add(self, response: Any) -> "RecordingTransport":
        self._script.append(response)
        return self

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": dict(body or {}),
                "timeout_ms": timeout_ms,
            }
        )
        if not self._script:
            if self._exhausted == "raise":
                raise ProviderUnavailableError("recording transport: script exhausted")
            return HttpResponse(status=200, headers={}, body=self._exhausted)
        item = self._script.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return item


class FailingTransport:
    """Raises a transient error on every request (retry/breaker negatives).

    ``failures`` optionally limits the failure burst: after ``failures``
    raised errors the transport returns ``success_body`` (fail-N-then-succeed
    for recovery tests). ``None`` (default) fails every request.
    """

    def __init__(
        self,
        error: Exception | None = None,
        *,
        failures: int | None = None,
        success_body: str = "{}",
    ) -> None:
        self._error = error or ProviderUnavailableError("provider unreachable")
        self._failures = failures
        self._success_body = success_body
        self.attempts = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> HttpResponse:
        self.attempts += 1
        if self._failures is None or self.attempts <= self._failures:
            raise self._error
        return HttpResponse(status=200, headers={}, body=self._success_body)


def now_ms() -> float:
    """Monotonic clock in milliseconds (shared timing helper)."""
    return time.monotonic() * 1000.0
