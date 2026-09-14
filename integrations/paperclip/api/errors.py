"""The boundary refusals the surface can emit, reusing the fleet's own codes.

Every refusal at this surface is one object — ``status`` + stable wire ``code``
+ ``message`` — and that object is the *same* :class:`~integrations.paperclip.auth.model.AuthError`
the merged auth seam already raises (#412). Nothing new is invented: the codes
are the fleet's own vocabulary, mapped not forked.

Three statuses the auth seam does not itself raise (a missing/out-of-scope
resource, a business-rule rejection, an unreachable dependency) are declared
here on the same shape, with codes taken verbatim from the fleet's canonical
error vocabulary (``identity/cpapi/errors.py``): ``not_found``, ``refused``,
``unavailable``. No refusal ever carries the credential or the value it refused
(GR-6).
"""

from __future__ import annotations

from ..auth.model import (  # noqa: F401  (re-exported: the boundary vocabulary)
    AuthError,
    cross_company,
    invalid_token,
    permission_denied,
    replayed_run_id,
    session_revoked,
    token_expired,
    unauthorized,
    validation_error,
)

__all__ = [
    "AuthError",
    "cross_company",
    "invalid_token",
    "not_found",
    "permission_denied",
    "refused",
    "replayed_run_id",
    "session_revoked",
    "token_expired",
    "unauthorized",
    "unavailable",
    "validation_error",
]


def not_found(message: str) -> AuthError:
    """``404`` — the resource is missing or outside the caller's company scope.

    Code is the fleet's own ``not_found`` (``identity/cpapi/errors.py``). A
    resource that exists but lies outside the caller's company is reported as
    *not found*, never as a forbidden existence oracle.
    """
    return AuthError(404, "not_found", message)


def refused(message: str) -> AuthError:
    """``422`` — the request is well-formed but breaks a business rule.

    Code is the fleet's own ``refused`` (``identity/cpapi/errors.py``, the
    fail-closed outcome vocabulary).
    """
    return AuthError(422, "refused", message)


def unavailable(message: str) -> AuthError:
    """``503`` — a dependency the surface names is unreachable.

    Code is the fleet's own ``unavailable`` (``identity/cpapi/errors.py``). The
    health surface raises this rather than reporting a green lie.
    """
    return AuthError(503, "unavailable", message)
