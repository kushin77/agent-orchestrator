"""Typed auth shapes and refusals for the paperclip boundary (issue #412).

The paperclip seam has two auth models on either side: upstream carries agent
keys/JWTs, a board session cookie and a board token; the fleet has no HTTP
caller identity at all (seam doc §5, mismatch #11). This module holds the shapes
that let the two meet **once** at the process boundary — the principal, the run
binding and the structured refusal.

No secret value is ever carried on a shape or into a refusal: a refusal names
the RULE (``status`` + ``code`` + ``message``), never the credential it refused
(GR-6). The wire-code vocabulary is the fleet's own (``identity/cpapi/errors.py``)
so a caller branches on one vocabulary rather than a forked one:

* ``400`` validation_error
* ``401`` unauthorized / invalid_token / token_expired / session_revoked
* ``403`` cross_tenant / permission_denied
* ``409`` replayed_run_id

``token_expired`` and ``replayed_run_id`` are the two additive codes this seam
contributes; every other code is the merged identity vocabulary verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

#: The upstream surface every agent token is minted for.
AUDIENCE = "paperclip"

#: The fleet issuer recorded in the ``iss`` claim of every token we mint.
ISSUER = "agent-orchestrator"

#: Principal kinds the boundary distinguishes.
KIND_AGENT = "agent"
KIND_HUMAN = "human"

#: HTTP methods that mutate upstream state (mirrors ``client.MUTATING_METHODS``).
MUTATING_METHODS = ("POST", "PATCH", "PUT", "DELETE")


@dataclass
class AuthError(Exception):
    """One structured refusal at the boundary.

    Deliberately not frozen: an exception carries mutable traceback state through
    the ``raise``/``except`` machinery (same rationale as ``identity/cpapi``).
    ``message`` never contains the refused credential.
    """

    status: int
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        Exception.__init__(self, f"{self.status} {self.code}: {self.message}")

    @property
    def reason(self) -> str:
        return self.code


# --------------------------------------------------------------------------
# 4xx constructors — the closed set of refusals this boundary can emit
# --------------------------------------------------------------------------


def validation_error(message: str, **details: Any) -> AuthError:
    return AuthError(400, "validation_error", message, details or None)


def unauthorized(message: str = "a caller identity is required") -> AuthError:
    return AuthError(401, "unauthorized", message)


def invalid_token(message: str = "the caller identity is invalid") -> AuthError:
    return AuthError(401, "invalid_token", message)


def token_expired() -> AuthError:
    return AuthError(401, "token_expired", "the caller identity has expired")


def session_revoked() -> AuthError:
    return AuthError(401, "session_revoked", "the board session was revoked")


def cross_company() -> AuthError:
    return AuthError(
        403,
        "cross_tenant",
        "the caller is scoped to a different company (no cross-company access)",
    )


def permission_denied(permission: str) -> AuthError:
    return AuthError(
        403,
        "permission_denied",
        f"missing permission {permission!r}",
        {"permission": permission},
    )


def replayed_run_id() -> AuthError:
    return AuthError(
        409,
        "replayed_run_id",
        "the run id was replayed (a run id maps to exactly one run)",
    )


# --------------------------------------------------------------------------
# Principals
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """A caller the boundary has authenticated and resolved.

    ``permissions`` is re-derived from the fleet's own record at verification
    time (never stored in the token), so revoking a capability in the registry
    takes effect on the next request — there is no stale authority to revoke.
    """

    kind: str
    subject: str
    company: str
    permissions: Tuple[str, ...] = ()
    profile_ref: str = ""
    session_id: str = ""
    roles: Tuple[str, ...] = ()

    @property
    def actor(self) -> str:
        """Ledger actor string ``kind:id`` (telemetry/ledger vocabulary)."""
        return f"{self.kind}:{self.subject}"


@dataclass(frozen=True)
class RunBinding:
    """One upstream run bound to the fleet's own ``correlation_id``."""

    run_id: str
    correlation_id: str


@dataclass(frozen=True)
class AuthContext:
    """What one guarded request resolved to: the principal and its run binding."""

    principal: Principal
    run: Optional[RunBinding] = None
    details: Dict[str, Any] = field(default_factory=dict)
