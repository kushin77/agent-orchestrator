"""Consumer SDK error taxonomy (issue #41).

The SDK raises a small closed family of exceptions.  ``ApiError`` carries the
platform's stable machine ``code`` plus the HTTP ``status`` so a caller can map
failures without string-matching; the codes are the envelope/error codes of the
merged control-plane REST contract (issue #38) and the gateway outcome/status
semantics (issue #16).  Nothing here performs I/O or carries a secret.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class SdkError(Exception):
    """Base class for all consumer-SDK errors."""


class ConfigurationError(SdkError):
    """The SDK was misconfigured (no token provider, no base URL, ...)."""


class TransportError(SdkError):
    """A transport-level failure (network, malformed response, ...)."""


class ApiError(SdkError):
    """A non-OK platform response, carrying the envelope error fields.

    ``status`` is the HTTP status semantic, ``code`` is the platform's stable
    machine error code (``scope_denied``/``permission_denied``/``not_found``/
    ``unknown_route``/... from the merged contracts), ``message`` is the
    human-readable summary and ``details`` any structured extra context.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(f"{status} {code}: {message}".strip())
        self.status = int(status)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class UnauthorizedError(ApiError):
    """HTTP 401 — the session token is missing, invalid, expired or revoked."""

    def __init__(self, message: str = "unauthenticated", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(401, "unauthenticated", message, details)


class ScopeDeniedError(ApiError):
    """HTTP 403 — the subject has no scope in the target tenant."""

    def __init__(self, message: str = "scope denied", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(403, "scope_denied", message, details)


class PermissionDeniedError(ApiError):
    """HTTP 403 — the subject is in scope but its role lacks the permission."""

    def __init__(self, message: str = "permission denied", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(403, "permission_denied", message, details)


class TaskNotServedError(SdkError):
    """A dispatched task finished with an explicit non-served outcome.

    ``result`` is the typed :class:`~aosdk.model.TaskResult` so the caller can
    inspect ``outcome``/``error`` and decide (retry, surface, ...).  The SDK
    never fabricates typed content for a non-served outcome (no-false-green).
    """

    def __init__(self, outcome: str, result: Any = None) -> None:
        self.outcome = outcome
        self.result = result
        super().__init__(f"task not served (outcome={outcome})")


class McpError(SdkError):
    """A JSON-RPC error reply from the MCP surface (issue #20).

    ``code`` is the JSON-RPC error code (``-32601`` method not found,
    ``-32602`` invalid params, ...); the gateway's enforcement denials surface
    as JSON-RPC errors carrying the cause.
    """

    def __init__(self, code: int, message: str = "", data: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(f"mcp error {code}: {message}".strip())
        self.code = int(code)
        self.message = message
        self.data = data or {}
