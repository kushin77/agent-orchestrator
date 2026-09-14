"""Human identity mapped onto the board session path (issue #412).

Upstream authenticates a human with a session cookie or a board token. The fleet
already has both halves of that on disk: an operator session is a
``governance/isolation`` ``SessionIdentity`` record (``.fleet/sessions/<id>.json``
at runtime, shape ``SessionIdentity.to_json()``) and the board is the claim
ledger (``.board/``). This module mints the board token **from that session
record** and verifies it against the same record, so an operator who already
holds a fleet session authenticates to the operator surface through it —
there is no third login and no parallel human-identity store.

The token carries the operator's session id and company scope; its permissions
are re-derived from the session roles on every verify.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from . import jwt as _jwt
from . import policy as _policy
from .model import (
    AUDIENCE,
    ISSUER,
    KIND_HUMAN,
    Principal,
    cross_company,
    invalid_token,
    session_revoked,
)

#: Default board-token lifetime (seconds) when a mint does not state one.
DEFAULT_TTL_SECONDS = 3600

#: The session-record field that carries the operator's fleet role set.
_ROLE_KEYS = ("roles", "role")


@dataclass(frozen=True)
class BoardSession:
    """The operator's existing fleet session, as the board token binds to it."""

    session_id: str
    operator: str
    company: str
    roles: Tuple[str, ...] = ("operator",)

    @classmethod
    def from_record(cls, record: Dict[str, Any], *, company: str) -> "BoardSession":
        """Build from a fleet session record (``SessionIdentity.to_json()`` shape).

        The operator identity is the record's ``agent_id`` (the fleet's session
        signature); a record may instead name an explicit ``operator``.
        """
        session_id = str(record.get("session_id") or "").strip()
        if not session_id:
            raise invalid_token("the board session record carries no session id")
        operator = str(record.get("operator") or record.get("agent_id") or "").strip()
        if not operator:
            raise invalid_token("the board session record names no operator")
        roles: Tuple[str, ...] = ("operator",)
        for key in _ROLE_KEYS:
            value = record.get(key)
            if isinstance(value, (list, tuple)) and value:
                roles = tuple(str(v) for v in value)
                break
            if isinstance(value, str) and value.strip():
                roles = (value.strip(),)
                break
        return cls(session_id=session_id, operator=operator, company=company, roles=roles)

    @property
    def permissions(self) -> Tuple[str, ...]:
        return _policy.permissions_for_operator(self.roles)


def load_operator_session(root: Path, session_id: str) -> Optional[Dict[str, Any]]:
    """Read an operator session record read-only from the runtime session path.

    Returns ``None`` when no such record exists; the caller decides whether its
    absence is a refusal (verification) or expected (minting from a fixture).
    """
    path = Path(root) / ".fleet" / "sessions" / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def mint_board_token(
    session: BoardSession,
    *,
    secret: str,
    now: Optional[int] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    jti: Optional[str] = None,
) -> str:
    """Mint a board token bound to an existing fleet operator session."""
    issued_at = int(time.time() if now is None else now)
    claims: Dict[str, object] = {
        "iss": issuer,
        "sub": session.operator,
        "aud": audience,
        "kind": KIND_HUMAN,
        "company": session.company,
        "session_id": session.session_id,
        "roles": list(session.roles),
        "iat": issued_at,
        "exp": issued_at + int(ttl_seconds),
        "jti": jti or str(uuid.uuid4()),
    }
    return _jwt.sign(claims, secret)


def verify_board_token(
    token: str,
    *,
    company: str,
    secret: str,
    now: int,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    leeway: int = _jwt.DEFAULT_LEEWAY,
    session_lookup: Optional[Callable[[str], bool]] = None,
) -> Principal:
    """Verify a board token, then re-check the board session it names.

    ``session_lookup`` answers "is this session still live?"; when supplied and it
    returns false the token is refused ``session_revoked`` — a revoked fleet
    session revokes the board token, because the session path, not the token, is
    authoritative for the human.
    """
    claims = _jwt.verify(
        token, secret=secret, now=now, audience=audience, issuer=issuer, leeway=leeway
    )
    if claims.get("kind") != KIND_HUMAN:
        raise invalid_token("the token is not a human identity")
    session_id = claims.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise invalid_token("the token carries no board session id")
    if claims.get("company") != company:
        raise cross_company()
    if session_lookup is not None and not session_lookup(session_id):
        raise session_revoked()
    roles = tuple(str(r) for r in (claims.get("roles") or ()) if str(r).strip())
    return Principal(
        kind=KIND_HUMAN,
        subject=str(claims["sub"]),
        company=company,
        permissions=_policy.permissions_for_operator(roles),
        session_id=session_id,
        roles=roles,
    )
