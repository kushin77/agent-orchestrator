"""Control-plane envelope handling (issue #38, consumed).

The control-plane REST surface answers every request with the standardized
envelope ``{ok, status, requestId, data, error}`` where ``error`` is
``{code, message, details}``.  This module unwraps that envelope into typed
values or raises the matching :class:`~aosdk.errors.ApiError` — fail closed,
never a silent pass on a non-OK envelope.  The gateway task envelope
(``{status, result, record}``, issue #16) is handled by the gateway client.
"""

from __future__ import annotations

from typing import Any, Mapping

from .errors import ApiError, UnauthorizedError, ScopeDeniedError, PermissionDeniedError


def error_from_envelope(
    envelope: Mapping[str, Any], *, fallback_code: str = "unknown_error"
) -> ApiError:
    """Build the typed :class:`ApiError` for a non-OK envelope."""
    status = int(envelope.get("status") or 500)
    raw_error = envelope.get("error") or {}
    code = str(raw_error.get("code") or fallback_code)
    message = str(raw_error.get("message") or "")
    details = raw_error.get("details")
    if status == 401 or code == "unauthenticated":
        return UnauthorizedError(message, dict(details) if isinstance(details, dict) else None)
    if status == 403:
        if code == "scope_denied":
            return ScopeDeniedError(message, dict(details) if isinstance(details, dict) else None)
        if code == "permission_denied":
            return PermissionDeniedError(message, dict(details) if isinstance(details, dict) else None)
    return ApiError(status, code, message, dict(details) if isinstance(details, dict) else None)


def require_ok(envelope: Mapping[str, Any]) -> Any:
    """Unwrap a control-plane envelope: return ``data`` or raise ``ApiError``.

    A truthy ``ok`` is required; a missing/false ``ok`` raises even when a
    status looks fine (the envelope is authoritative).
    """
    if not isinstance(envelope, Mapping):
        raise ApiError(500, "malformed_response", "response was not an envelope object")
    if not envelope.get("ok"):
        raise error_from_envelope(envelope)
    return envelope.get("data")


def items(data: Any) -> list:
    """Return the ``items`` list of a list-style payload (``{"items": [...]}``)."""
    if isinstance(data, Mapping):
        listed = data.get("items")
        if isinstance(listed, list):
            return listed
    if isinstance(data, list):
        return data
    return []
