"""Agent identity is minted and verified from the fleet's own registry (#412)."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.auth import jwt as jwt_mod
from integrations.paperclip.auth import registry as registry_mod
from integrations.paperclip.auth.model import AuthError


def test_registered_agents_read_from_seeds(root: Path) -> None:
    agents = registry_mod.registered_agents(root)
    # the purebliss team seeds are the registry's own records
    assert {"paperclip", "orchestrator", "claude"}.issubset(agents)
    assert agents["paperclip"].owner == "platform/purebliss"
    # a profile reference is `<id>@<version>` (registry/service/catalog.resolve_profile)
    assert agents["paperclip"].profile_ref == "paperclip@1.0.0"


def test_mint_and_verify_round_trip(root: Path, company: str, key: str, now: int) -> None:
    token = registry_mod.mint_agent_key(root, "paperclip", company=company, secret=key, now=now)
    principal = registry_mod.verify_agent_key(root, token, company=company, secret=key, now=now)
    assert principal.kind == "agent"
    assert principal.subject == "paperclip"
    assert principal.company == company
    assert "run:write" in principal.permissions


def test_permissions_are_derived_from_the_record(root: Path, company: str, key: str, now: int) -> None:
    # paperclip has no `orchestrate` capability -> no approval power
    paperclip = registry_mod.verify_agent_key(
        root,
        registry_mod.mint_agent_key(root, "paperclip", company=company, secret=key, now=now),
        company=company,
        secret=key,
        now=now,
    )
    assert "approval:request" not in paperclip.permissions
    # orchestrator does -> approval power, re-derived from the record
    orchestrator = registry_mod.verify_agent_key(
        root,
        registry_mod.mint_agent_key(root, "orchestrator", company=company, secret=key, now=now),
        company=company,
        secret=key,
        now=now,
    )
    assert "approval:request" in orchestrator.permissions


def test_unregistered_agent_cannot_be_minted(root: Path, company: str, key: str, now: int) -> None:
    with pytest.raises(AuthError) as exc:
        registry_mod.mint_agent_key(root, "ghost-agent", company=company, secret=key, now=now)
    assert exc.value.status == 401
    assert "not bound to a registered agent" in exc.value.message


def test_verify_re_reads_registry_not_the_token(root: Path, company: str, key: str, now: int) -> None:
    # a well-signed token whose subject is not a registered agent is refused:
    # the registry, not the token, is the source of truth
    token = jwt_mod.sign(
        {
            "iss": "agent-orchestrator",
            "sub": "ghost-agent",
            "aud": "paperclip",
            "kind": "agent",
            "company": company,
            "iat": now,
            "exp": now + 900,
            "jti": "t1",
        },
        key,
    )
    with pytest.raises(AuthError) as exc:
        registry_mod.verify_agent_key(root, token, company=company, secret=key, now=now)
    assert exc.value.status == 401
    assert "not bound to a registered agent" in exc.value.message


def test_wrong_company_refused(root: Path, company: str, key: str, now: int) -> None:
    token = registry_mod.mint_agent_key(root, "paperclip", company="other-company", secret=key, now=now)
    with pytest.raises(AuthError) as exc:
        registry_mod.verify_agent_key(root, token, company=company, secret=key, now=now)
    assert exc.value.status == 403
    assert exc.value.code == "cross_tenant"


def test_human_token_is_not_an_agent_token(root: Path, company: str, key: str, now: int) -> None:
    token = jwt_mod.sign(
        {
            "iss": "agent-orchestrator",
            "sub": "operator",
            "aud": "paperclip",
            "kind": "human",
            "company": company,
            "session_id": "s1",
            "iat": now,
            "exp": now + 900,
            "jti": "t2",
        },
        key,
    )
    with pytest.raises(AuthError):
        registry_mod.verify_agent_key(root, token, company=company, secret=key, now=now)
