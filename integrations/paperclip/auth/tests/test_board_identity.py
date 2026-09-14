"""Human identity maps onto the fleet's board session path (#412)."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.auth import board as board_mod
from integrations.paperclip.auth.model import AuthError


def test_session_from_record(session_record: dict, company: str) -> None:
    session = board_mod.BoardSession.from_record(session_record, company=company)
    assert session.session_id == "sess-operator-1"
    assert session.operator == "operator"
    assert session.roles == ("operator",)


def test_record_needs_a_session_id(company: str) -> None:
    with pytest.raises(AuthError) as exc:
        board_mod.BoardSession.from_record({"agent_id": "operator"}, company=company)
    assert exc.value.status == 401


def test_mint_and_verify(session_record: dict, company: str, key: str, now: int) -> None:
    session = board_mod.BoardSession.from_record(session_record, company=company)
    token = board_mod.mint_board_token(session, secret=key, now=now)
    principal = board_mod.verify_board_token(
        token, company=company, secret=key, now=now, session_lookup=lambda sid: True
    )
    assert principal.kind == "human"
    assert principal.subject == "operator"
    assert principal.session_id == "sess-operator-1"
    # operators may request approvals but never mint identity keys
    assert "approval:request" in principal.permissions
    assert "agent:mint" not in principal.permissions


def test_revoked_session_refused(session_record: dict, company: str, key: str, now: int) -> None:
    session = board_mod.BoardSession.from_record(session_record, company=company)
    token = board_mod.mint_board_token(session, secret=key, now=now)
    with pytest.raises(AuthError) as exc:
        board_mod.verify_board_token(
            token, company=company, secret=key, now=now, session_lookup=lambda sid: False
        )
    assert exc.value.status == 401
    assert exc.value.code == "session_revoked"


def test_wrong_company_refused(session_record: dict, company: str, key: str, now: int) -> None:
    session = board_mod.BoardSession.from_record(session_record, company="other-company")
    token = board_mod.mint_board_token(session, secret=key, now=now)
    with pytest.raises(AuthError) as exc:
        board_mod.verify_board_token(token, company=company, secret=key, now=now)
    assert exc.value.status == 403
    assert exc.value.code == "cross_tenant"


def test_load_operator_session_missing_returns_none(root: Path) -> None:
    assert board_mod.load_operator_session(root, "no-such-session") is None
