"""Shape-level tests for the secret vault primitive (issue #417)."""

from __future__ import annotations

from integrations.paperclip.adapters.secrets.model import (
    GSM_STORE,
    SECRET_WILDCARD,
    Caller,
    SecretRef,
    ref_from_declaration,
)


def test_secret_ref_view_shape_is_name_only():
    ref = SecretRef(
        gsm_path="projects/example/secrets/agent-x-api-credential",
        agent="agent-x",
        scope="agent:agent-x",
        consumer="agent-x",
        last_rotated_at="2026-09-01T00:00:00Z",
    )
    view = ref.to_view()
    assert set(view) == {
        "gsm_path",
        "agent",
        "scope",
        "store",
        "consumer",
        "last_rotated_at",
        "rotated",
    }
    assert view["store"] == GSM_STORE
    assert view["rotated"] is True


def test_secret_ref_has_no_value_field_by_construction():
    ref = SecretRef(gsm_path="projects/example/secrets/x", agent="a", scope="agent:a")
    assert not hasattr(ref, "value")
    assert "value" not in ref.to_view()


def test_never_rotated_secret_is_visible_as_such():
    ref = SecretRef(gsm_path="projects/example/secrets/x", agent="a", scope="agent:a", consumer="a")
    assert ref.rotated is False
    assert ref.to_view()["last_rotated_at"] is None


def test_orphaned_when_no_consumer():
    base = dict(gsm_path="projects/example/secrets/x", agent="a", scope="agent:a")
    assert SecretRef(**base).orphaned is True
    assert SecretRef(**base, consumer="").orphaned is True
    assert SecretRef(**base, consumer="   ").orphaned is True
    assert SecretRef(**base, consumer="a").orphaned is False


def test_ref_from_declaration_reads_only_name_keys():
    declaration = {
        "gsm_path": "projects/example/secrets/x",
        "agent": "a",
        "scope": "agent:a",
        "consumer": "a",
        "last_rotated_at": "2026-01-01T00:00:00Z",
        "value": "fake-placeholder-never-read",
    }
    ref = ref_from_declaration(declaration)
    assert ref.gsm_path == "projects/example/secrets/x"
    assert "fake-placeholder-never-read" not in str(ref.to_view())


def test_caller_authenticated_requires_a_principal():
    assert Caller.of("").authenticated is False
    assert Caller.of("   ").authenticated is False
    assert Caller.of("session-1").authenticated is True


def test_caller_may_read_is_least_privilege():
    assert Caller.of("s", ["agent:agent-x"]).may_read("agent:agent-x") is True
    assert Caller.of("s", ["agent:agent-x"]).may_read("agent:agent-y") is False
    assert Caller.of("s", []).may_read("agent:agent-x") is False
    assert Caller.of("s", [SECRET_WILDCARD]).may_read("agent:agent-x") is True
    assert Caller.of("s", ["secret:read"]).may_read("agent:agent-x") is False
