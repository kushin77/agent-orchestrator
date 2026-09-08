"""Plan catalog: parse, validation, and the entitlement -> RBAC mapping.

The catalog is product configuration; it must load strictly (fail closed on
schema or referential violations) and declare the capability tiering the rest
of the suite asserts on.
"""

import pytest

from entitlements import FEATURE_KIND_FEATURE, FEATURE_KIND_LIMIT
from entitlements import parse_catalog

# A minimal valid catalog used to exercise validation error paths.
_MINIMAL = """
version: 1
features:
  - key: budgets
    kind: feature
    grants: [budget:read, budget:manage]
  - key: agents
    kind: limit
    grants: [agent:create]
plans:
  - key: pro
    name: Pro
    entitlements:
      - feature: budgets
        enabled: true
      - feature: agents
        enabled: true
        limit: 10
"""


def test_default_catalog_has_three_plans(catalog):
    assert catalog.plan_keys == ("free", "pro", "enterprise")
    assert set(catalog.feature_keys) == {
        "managed_agents",
        "multi_team",
        "budgets",
        "model_routing",
        "tool_governance",
        "audit_log",
        "api_access",
        "sso",
    }


def test_plan_toggles_and_limits(catalog):
    free = catalog.plan("free")
    pro = catalog.plan("pro")
    enterprise = catalog.plan("enterprise")
    # managed_agents is on every tier, capacity-capped (None == unlimited).
    assert (free.entitlement("managed_agents").enabled, free.entitlement("managed_agents").limit) == (True, 3)
    assert (pro.entitlement("managed_agents").enabled, pro.entitlement("managed_agents").limit) == (True, 25)
    assert (enterprise.entitlement("managed_agents").enabled, enterprise.entitlement("managed_agents").limit) == (True, None)
    # Feature-tier tiering.
    assert free.entitlement("audit_log").enabled is False
    assert pro.entitlement("audit_log").enabled is True
    assert enterprise.entitlement("sso").enabled is True
    assert free.entitlement("sso").enabled is False


def test_feature_kinds_are_declared(catalog):
    assert catalog.feature("managed_agents").kind == FEATURE_KIND_LIMIT
    for key in ("multi_team", "budgets", "model_routing", "tool_governance",
                "audit_log", "api_access", "sso"):
        assert catalog.feature(key).kind == FEATURE_KIND_FEATURE


def test_grants_map_features_to_permissions(catalog):
    # The entitlement -> RBAC mapping half of the contract.
    assert set(catalog.feature("budgets").grants) == {"budget:read", "budget:manage"}
    assert set(catalog.feature("audit_log").grants) == {"audit:read"}
    assert set(catalog.feature("model_routing").grants) == {"model:read", "model:manage"}
    assert set(catalog.feature("multi_team").grants) == {
        "team:manage", "member:invite", "member:remove",
    }
    # Pure surface flags gate nothing in RBAC.
    assert catalog.feature("api_access").grants == ()
    assert catalog.feature("sso").grants == ()


def test_parse_accepts_minimal_catalog():
    parsed = parse_catalog(_MINIMAL)
    assert parsed.plan("pro").entitlement("agents").limit == 10


def test_parse_rejects_unknown_feature_reference():
    text = _MINIMAL.replace("feature: agents", "feature: nope", 1)
    with pytest.raises(ValueError):
        parse_catalog(text)


def test_parse_rejects_duplicate_feature_in_plan():
    dup = """
version: 1
features:
  - key: budgets
    kind: feature
plans:
  - key: pro
    entitlements:
      - feature: budgets
        enabled: true
      - feature: budgets
        enabled: false
"""
    with pytest.raises(ValueError):
        parse_catalog(dup)


def test_parse_rejects_duplicate_plan_key():
    dup = """
version: 1
features:
  - key: budgets
    kind: feature
plans:
  - key: pro
    name: Pro
    entitlements:
      - feature: budgets
        enabled: true
  - key: pro
    name: Pro Again
    entitlements:
      - feature: budgets
        enabled: true
"""
    with pytest.raises(ValueError):
        parse_catalog(dup)


def test_parse_rejects_invalid_permission_grant():
    text = _MINIMAL.replace("budget:read, budget:manage", "not-a-permission")
    with pytest.raises(ValueError):
        parse_catalog(text)


def test_parse_rejects_limit_on_feature_kind():
    text = """
version: 1
features:
  - key: budgets
    kind: feature
plans:
  - key: pro
    entitlements:
      - feature: budgets
        enabled: true
        limit: 5
"""
    with pytest.raises(ValueError):
        parse_catalog(text)


def test_parse_rejects_non_positive_limit():
    text = _MINIMAL.replace("limit: 10", "limit: 0")
    with pytest.raises(ValueError):
        parse_catalog(text)


def test_parse_rejects_duplicate_feature_key():
    dup = """
version: 1
features:
  - key: budgets
    kind: feature
  - key: budgets
    kind: feature
plans:
  - key: pro
    entitlements:
      - feature: budgets
        enabled: true
"""
    with pytest.raises(ValueError):
        parse_catalog(dup)


def test_catalog_requires_a_plan():
    text = """
version: 1
features:
  - key: budgets
    kind: feature
plans: []
"""
    with pytest.raises(ValueError):
        parse_catalog(text)
