"""Permission language: allow/deny including wildcards (issue #12 criterion 1)."""

import pytest

from rbac.model import (
    Role,
    format_permission,
    is_permission,
    permission_granted,
    role_grants,
    split_permission,
)


def _role(permissions):
    return Role(
        id="r1",
        org_id="acme",
        key="test",
        name="Test",
        permissions=tuple(permissions),
    )


# --- construction -----------------------------------------------------------


def test_format_permission_combines_segments():
    assert format_permission("agent", "run") == "agent:run"
    assert format_permission("*", "read") == "*:read"


@pytest.mark.parametrize(
    "resource,action",
    [
        ("", "read"),
        ("agent", ""),
        ("agent:ops", "run"),
        ("agent", "run:now"),
    ],
)
def test_format_permission_rejects_malformed_segments(resource, action):
    with pytest.raises(ValueError):
        format_permission(resource, action)


def test_split_and_is_permission():
    assert split_permission("agent:run") == ("agent", "run")
    assert is_permission("agent:run")
    assert is_permission("*:*")
    assert is_permission("agent:*")
    assert not is_permission("agent")
    assert not is_permission("agent:run:extra")
    assert not is_permission(":run")
    assert not is_permission("agent:")
    assert not is_permission("")
    assert not is_permission(123)  # type: ignore[arg-type]


# --- matching (wildcards on the granted side only) ---------------------------


def test_permission_granted_exact_match():
    assert permission_granted("agent:run", "agent:run")
    assert not permission_granted("agent:read", "agent:run")


def test_permission_granted_resource_wildcard():
    # granted "agent:*" covers every action on agent
    assert permission_granted("agent:*", "agent:run")
    assert permission_granted("agent:*", "agent:delete")
    assert not permission_granted("agent:*", "model:run")


def test_permission_granted_action_wildcard():
    # granted "*:run" covers run on every resource
    assert permission_granted("*:run", "agent:run")
    assert permission_granted("*:run", "model:run")
    assert not permission_granted("*:run", "agent:read")


def test_permission_granted_full_wildcard():
    assert permission_granted("*:*", "anything:at_all")


def test_permission_granted_denies_partial_mismatch():
    assert not permission_granted("agent:read", "agent:write")
    assert not permission_granted("model:read", "agent:read")
    assert not permission_granted("agent:*", "session:read")


def test_permission_granted_malformed_is_deny_not_crash():
    assert not permission_granted("nope", "agent:run")
    assert not permission_granted("agent:run", "nope")
    assert not permission_granted("", "")


# --- roles ------------------------------------------------------------------


def test_role_grants_honors_wildcards():
    role = _role(["*:*"])
    assert role_grants(role, "roles:manage")
    assert role_grants(role, "agent:run")

    limited = _role(["agent:*", "session:read"])
    assert role_grants(limited, "agent:run")
    assert role_grants(limited, "session:read")
    assert not role_grants(limited, "model:manage")


def test_role_rejects_malformed_permission_at_construction():
    with pytest.raises(ValueError):
        Role(
            id="r",
            org_id="acme",
            key="bad",
            name="Bad",
            permissions=("agent:run", "no-colon-here"),
        )
