"""The boundary pipeline: authN -> scope -> authZ -> run correlation (#412).

These tests drive the whole pipeline (``guard.guard_request``) rather than its
parts, because the order of the gates is the property under test: the missing
header, the expired token, the cross-company token, the replayed run id and the
authenticated-but-not-allowed caller must each be refused **by name**, and
"not allowed" must be a 403 that is plainly not a 404.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.auth import board as board_mod
from integrations.paperclip.auth import guard as guard_mod
from integrations.paperclip.auth import jwt as jwt_mod
from integrations.paperclip.auth import registry as registry_mod
from integrations.paperclip.auth import runbridge as runbridge_mod
from integrations.paperclip.auth.model import AuthError


def _agent_headers(root: Path, company: str, key: str, now: int) -> dict:
    token = registry_mod.mint_agent_key(root, "paperclip", company=company, secret=key, now=now)
    return {"Authorization": f"Bearer {token}"}


def test_missing_authorization_is_401_unauthorized(
    root: Path, company: str, key: str, now: int
) -> None:
    with pytest.raises(AuthError) as exc:
        guard_mod.guard_request(
            "GET", {}, root=root, company=company, secret=key, now=now, permission="run:read"
        )
    assert exc.value.status == 401
    assert exc.value.code == "unauthorized"


def test_non_bearer_scheme_is_refused(root: Path, company: str, key: str, now: int) -> None:
    with pytest.raises(AuthError) as exc:
        guard_mod.guard_request(
            "GET",
            {"Authorization": "Basic dXNlcjpwYXNz"},
            root=root,
            company=company,
            secret=key,
            now=now,
            permission="run:read",
        )
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"


def test_agent_request_resolves_to_a_principal(
    root: Path, company: str, key: str, now: int
) -> None:
    context = guard_mod.guard_request(
        "GET",
        _agent_headers(root, company, key, now),
        root=root,
        company=company,
        secret=key,
        now=now,
        permission="run:read",
    )
    assert context.principal.actor == "agent:paperclip"
    assert context.principal.company == company
    assert context.run is None


def test_authenticated_but_not_allowed_is_403_not_404(
    root: Path, company: str, key: str, now: int
) -> None:
    # paperclip is a known caller on a known route; it simply lacks the
    # permission. That is a denial (403), never a missing resource (404).
    with pytest.raises(AuthError) as exc:
        guard_mod.guard_request(
            "POST",
            _agent_headers(root, company, key, now),
            root=root,
            company=company,
            secret=key,
            now=now,
            permission="approval:request",
        )
    assert exc.value.status == 403
    assert exc.value.status != 404
    assert exc.value.code == "permission_denied"
    assert "approval:request" in exc.value.message


def test_unknown_permission_is_not_a_404(root: Path, company: str, key: str, now: int) -> None:
    # "no such route" is not the same refusal as "not allowed": an undeclared
    # permission is a programming error, so the gate cannot confuse the two.
    principal = registry_mod.verify_agent_key(
        root,
        registry_mod.mint_agent_key(root, "paperclip", company=company, secret=key, now=now),
        company=company,
        secret=key,
        now=now,
    )
    with pytest.raises(ValueError):
        guard_mod.authorize(principal, "no:such-permission")


def test_cross_company_request_refused(root: Path, company: str, key: str, now: int) -> None:
    headers = _agent_headers(root, company, key, now)
    with pytest.raises(AuthError) as exc:
        guard_mod.guard_request(
            "GET",
            headers,
            root=root,
            company="other-company",
            secret=key,
            now=now,
            permission="run:read",
        )
    assert exc.value.status == 403
    assert exc.value.code == "cross_tenant"


def test_mutating_request_binds_run_id_to_correlation(
    root: Path, company: str, key: str, now: int
) -> None:
    headers = _agent_headers(root, company, key, now)
    headers["X-Paperclip-Run-Id"] = "run-abc"
    bridge = runbridge_mod.RunBridge()
    context = guard_mod.guard_request(
        "POST",
        headers,
        root=root,
        company=company,
        secret=key,
        now=now,
        permission="run:write",
        bridge=bridge,
        correlation_id="corr-42",
    )
    assert context.run is not None
    assert context.run.run_id == "run-abc"
    assert context.run.correlation_id == "corr-42"
    assert bridge.run_for("corr-42") == "run-abc"


def test_replayed_run_id_refused_at_the_boundary(
    root: Path, company: str, key: str, now: int
) -> None:
    headers = _agent_headers(root, company, key, now)
    headers["X-Paperclip-Run-Id"] = "run-dup"
    bridge = runbridge_mod.RunBridge()
    kwargs = dict(
        root=root, company=company, secret=key, now=now, permission="run:write", bridge=bridge
    )
    guard_mod.guard_request("POST", headers, **kwargs)
    with pytest.raises(AuthError) as exc:
        guard_mod.guard_request("POST", headers, **kwargs)
    assert exc.value.status == 409
    assert exc.value.code == "replayed_run_id"


def test_run_id_on_a_read_is_refused(root: Path, company: str, key: str, now: int) -> None:
    headers = _agent_headers(root, company, key, now)
    headers["X-Paperclip-Run-Id"] = "run-read"
    with pytest.raises(AuthError) as exc:
        guard_mod.guard_request(
            "GET",
            headers,
            root=root,
            company=company,
            secret=key,
            now=now,
            permission="run:read",
            bridge=runbridge_mod.RunBridge(),
        )
    assert exc.value.status == 400


def test_board_token_routes_to_the_human_verifier(
    root: Path, company: str, key: str, now: int
) -> None:
    session = board_mod.BoardSession.from_record(
        {"session_id": "sess-op", "agent_id": "operator"}, company=company
    )
    token = board_mod.mint_board_token(session, secret=key, now=now)
    context = guard_mod.guard_request(
        "GET",
        {"Authorization": f"Bearer {token}"},
        root=root,
        company=company,
        secret=key,
        now=now,
        permission="approval:request",
        session_lookup=lambda sid: True,
    )
    assert context.principal.kind == "human"


def test_unrecognised_kind_is_refused(root: Path, company: str, key: str, now: int) -> None:
    token = jwt_mod.sign(
        {
            "iss": "agent-orchestrator",
            "sub": "x",
            "aud": "paperclip",
            "kind": "robot",
            "company": company,
            "iat": now,
            "exp": now + 900,
            "jti": "k1",
        },
        key,
    )
    with pytest.raises(AuthError) as exc:
        guard_mod.guard_request(
            "GET",
            {"Authorization": f"Bearer {token}"},
            root=root,
            company=company,
            secret=key,
            now=now,
            permission="run:read",
        )
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"
