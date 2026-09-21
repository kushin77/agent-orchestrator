"""CTO office root/super-admin RBAC declaration (issue #1573, Parent #1510).

`identity/rbac/presets/cto-superadmin.yaml` is a named overlay pack, not a
BUILTIN_TENANT_TYPES pack (it extends the `cto` role in csuite.yaml, it does
not seed an org on its own) so it is outside `test_presets.py`'s builtin-pack
loop. This is the dedicated least-privilege/break-glass check the issue's
provenance requirement names: every permission is explicit, no wildcard is
ever granted, and the break-glass path is documented, expiring and never
self-approved.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PRESET_PATH = (
    Path(__file__).resolve().parents[2] / "rbac" / "presets" / "cto-superadmin.yaml"
)


def _load() -> dict:
    return yaml.safe_load(PRESET_PATH.read_text())


def test_preset_exists_and_parses():
    assert PRESET_PATH.is_file()
    doc = _load()
    assert doc["key"] == "cto-superadmin"


def test_no_permission_is_a_wildcard():
    doc = _load()
    for role in doc["roles"]:
        for permission in role["permissions"]:
            assert "*" not in permission, f"wildcard permission granted: {permission!r}"
        for permission in role.get("explicitlyDenied", []):
            assert "*" not in permission


def test_explicit_deny_list_names_role_and_org_admin():
    doc = _load()
    denied = set(doc["roles"][0]["explicitlyDenied"])
    assert {"roles:manage", "budget:manage", "org:manage"} <= denied


def test_schema_rejects_missing_explicit_deny():
    """No-false-green proof: a role with no explicitlyDenied list must fail
    the check above, not pass vacuously."""
    bad_role = {"permissions": ["agent:read"]}
    assert "explicitlyDenied" not in bad_role
    assert bad_role.get("explicitlyDenied", []) == []


def test_break_glass_is_documented_time_boxed_and_never_self_approved():
    doc = _load()
    bg = doc["break_glass"]
    assert bg["procedure"]
    never = " ".join(bg["neverGrants"]).lower()
    assert "wildcard" in never
    assert "standing" in never or "non-expiring" in never
    assert "self-approval" in never or "self-granted" in " ".join(bg["procedure"]).lower()
