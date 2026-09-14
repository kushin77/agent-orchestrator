"""Agent identity minted and verified from the fleet's OWN registry (issue #412).

An agent key/JWT is **bound to a registered agent**. The registry seeds under
``registry/profiles/seeds/*.yaml`` are the single source of truth (the same
records ``integrations/paperclip/mapping.py`` projects upstream): the mint looks
the agent up there and refuses an unregistered agent, and the verify re-reads the
record so a token whose agent has since been retired is refused too. There is no
second identity store and no cached authority (ADR-0012 — map the policy, do not
couple the runtime).

The claim carries the **company/tenant scope** (``company``) alongside the
subject; it carries no permissions — those are re-derived from the record on
every verify, so the record, not the token, decides what an agent may do.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .. import mapping as _mapping
from . import jwt as _jwt
from . import policy as _policy
from .model import (
    AUDIENCE,
    ISSUER,
    KIND_AGENT,
    Principal,
    cross_company,
    invalid_token,
)

#: Default token lifetime (seconds) when a mint does not state one.
DEFAULT_TTL_SECONDS = 900


@dataclass(frozen=True)
class AgentRecord:
    """One registered agent, as read from the fleet registry seeds."""

    agent_id: str
    version: str
    owner: str
    tier: str
    capabilities: Tuple[str, ...]
    tools: Tuple[str, ...]
    constraints: Tuple[str, ...]

    @property
    def profile_ref(self) -> str:
        """The pinned ``<id>@v<version>`` reference recorded in the claim."""
        return f"{self.agent_id}@{self.version or '0.0.0'}"

    @property
    def permissions(self) -> Tuple[str, ...]:
        return _policy.permissions_for_agent(self.capabilities, self.tools)


def registered_agents(root: Path) -> Dict[str, AgentRecord]:
    """Every registered agent, keyed by id — read live from the seeds."""
    records: Dict[str, AgentRecord] = {}
    for profile in _mapping.iter_seed_profiles(Path(root)):
        records[profile.id] = AgentRecord(
            agent_id=profile.id,
            version=str(profile.version),
            owner=str(profile.owner),
            tier=str(profile.default_model_tier),
            capabilities=tuple(profile.capabilities),
            tools=tuple(profile.tools),
            constraints=tuple(profile.constraints),
        )
    return records


def get_registered_agent(root: Path, agent_id: str) -> Optional[AgentRecord]:
    """The record for ``agent_id``, or ``None`` when the agent is not registered."""
    return registered_agents(root).get(agent_id)


def mint_agent_key(
    root: Path,
    agent_id: str,
    *,
    company: str,
    secret: str,
    now: Optional[int] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    jti: Optional[str] = None,
) -> str:
    """Mint an agent key bound to a registered agent, scoped to ``company``.

    An agent absent from the registry cannot be minted for: identity comes from
    the fleet's own records, never from the caller (GR-6 aside, this is the
    no-second-source-of-truth rule, ADR-0012).
    """
    if not company:
        raise invalid_token("an agent identity must name a company scope")
    record = get_registered_agent(root, agent_id)
    if record is None:
        raise invalid_token("the agent identity is not bound to a registered agent")
    issued_at = int(time.time() if now is None else now)
    claims: Dict[str, object] = {
        "iss": issuer,
        "sub": record.agent_id,
        "aud": audience,
        "kind": KIND_AGENT,
        "company": company,
        "profile_ref": record.profile_ref,
        "owner": record.owner,
        "iat": issued_at,
        "exp": issued_at + int(ttl_seconds),
        "jti": jti or str(uuid.uuid4()),
    }
    return _jwt.sign(claims, secret)


def verify_agent_key(
    root: Path,
    token: str,
    *,
    company: str,
    secret: str,
    now: int,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    leeway: int = _jwt.DEFAULT_LEEWAY,
) -> Principal:
    """Verify an agent key: signature, then the record it claims, then scope.

    Refused, in order and by name: a bad/expired token (401), a token that is
    not an agent identity (401), a subject that is no longer a registered agent
    (401), and a token whose company scope is not the company the request is for
    (403 ``cross_tenant``). Fail closed throughout.
    """
    claims = _jwt.verify(
        token, secret=secret, now=now, audience=audience, issuer=issuer, leeway=leeway
    )
    if claims.get("kind") != KIND_AGENT:
        raise invalid_token("the token is not an agent identity")
    subject = claims["sub"]
    record = get_registered_agent(root, subject)
    if record is None:
        raise invalid_token("the agent identity is not bound to a registered agent")
    if claims.get("company") != company:
        # the caller is authenticated, just not for THIS company
        raise cross_company()
    return Principal(
        kind=KIND_AGENT,
        subject=record.agent_id,
        company=company,
        permissions=record.permissions,
        profile_ref=record.profile_ref,
        roles=(),
    )
