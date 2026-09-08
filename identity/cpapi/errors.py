"""Control-plane REST API error taxonomy.

Every failure that crosses the API boundary is an :class:`ApiError` carrying an
HTTP status and a stable machine ``code`` (mirroring the AUTH_ERRORS wire-code
convention of the merged identity lanes and the gateway proxy outcome codes).
Handlers raise these; the facade maps them to the standardized error envelope
with no other exception escaping as a 500.

Codes are grouped by status so a client can branch mechanically:

- ``400``  invalid_request / validation_error / invalid_body
- ``401``  unauthorized / invalid_token / session_revoked / cross_tenant
- ``403``  forbidden / scope_denied / permission_denied
- ``404``  not_found / unknown_* (unknown_agent, unknown_tenant, ...)
- ``409``  conflict / already_registered / duplicate
- ``422``  cannot_assess / refused (fail-closed outcome vocabulary)
- ``503``  unavailable / approval_required (a destructive action needs an
  approver before it can run)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ApiError(Exception):
    """One structured API failure. ``code`` is a stable machine string.

    Deliberately not frozen: exceptions carry mutable traceback state through
    the ``raise``/``except`` machinery, so freezing the dataclass breaks
    propagation (``FrozenInstanceError`` on ``__traceback__`` assignment).
    """

    status: int
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        Exception.__init__(self, f"{self.status} {self.code}: {self.message}")


# --- 4xx helpers ------------------------------------------------------------


def validation_error(message: str, **details: Any) -> ApiError:
    return ApiError(400, "validation_error", message, details or None)


def invalid_body(message: str) -> ApiError:
    return ApiError(400, "invalid_body", message)


def unauthorized(message: str = "a valid session token is required") -> ApiError:
    return ApiError(401, "unauthorized", message)


def invalid_token(message: str = "the session token is invalid or expired") -> ApiError:
    return ApiError(401, "invalid_token", message)


def session_revoked() -> ApiError:
    return ApiError(401, "session_revoked", "the session was revoked")


def cross_tenant() -> ApiError:
    return ApiError(
        403,
        "cross_tenant",
        "the session is scoped to a different tenant (no cross-tenant access)",
    )


def forbidden(message: str = "not authorized for this operation") -> ApiError:
    return ApiError(403, "forbidden", message)


def scope_denied(code: str = "out_of_scope") -> ApiError:
    return ApiError(
        403,
        "scope_denied",
        "the principal is not in scope for this tenant",
        {"reason": code},
    )


def permission_denied(permission: str, missing: Any = None) -> ApiError:
    return ApiError(
        403,
        "permission_denied",
        f"missing permission {permission!r}",
        {"permission": permission, "missingPermissions": missing or [permission]},
    )


def not_found(message: str, code: str = "not_found") -> ApiError:
    return ApiError(404, code, message)


def conflict(message: str, code: str = "conflict") -> ApiError:
    return ApiError(409, code, message)


def refused(message: str) -> ApiError:
    return ApiError(422, "refused", message)


def unavailable(message: str) -> ApiError:
    return ApiError(503, "unavailable", message)


def approval_required(approval_id: str) -> ApiError:
    return ApiError(
        503,
        "approval_required",
        "this destructive action needs an approved authorization request",
        {"approvalId": approval_id},
    )
