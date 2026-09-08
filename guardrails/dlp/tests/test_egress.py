"""Egress-guard tests (default-deny per-tenant allowlist).

Only allowlisted providers/endpoints are reachable from a tenant context.
An empty allowlist denies everything; an unlisted tenant/provider/host/path is
denied; host matching is exact-or-subdomain and never allows suffix-squatting
names; tenant isolation means one tenant cannot reach another tenant's targets.
"""

from __future__ import annotations

from dlp.egress import AllowedTarget, EgressGuard


def _guard(**tenants) -> EgressGuard:
    return EgressGuard(
        {
            tenant: targets
            for tenant, targets in tenants.items()
        }
    )


OPENAI_EXACT = AllowedTarget("openai", "api.openai.com", "/v1/chat/completions")


def test_default_deny_with_no_allowlist():
    guard = EgressGuard()
    decision = guard.allow(
        tenant_id="acme", provider="openai", endpoint="https://api.openai.com/v1/chat/completions"
    )
    assert not decision.allowed
    assert "no egress allowlist" in decision.reason


def test_allowlisted_exact_endpoint_allows():
    guard = _guard(acme=[OPENAI_EXACT])
    decision = guard.allow(
        tenant_id="acme",
        provider="openai",
        endpoint="https://api.openai.com/v1/chat/completions",
    )
    assert decision.allowed


def test_unlisted_tenant_is_denied():
    guard = _guard(acme=[OPENAI_EXACT])
    decision = guard.allow(
        tenant_id="globex", provider="openai", endpoint="https://api.openai.com/v1/chat/completions"
    )
    assert not decision.allowed


def test_unlisted_provider_is_denied():
    guard = _guard(acme=[OPENAI_EXACT])
    decision = guard.allow(
        tenant_id="acme",
        provider="anthropic",
        endpoint="https://api.anthropic.com/v1/messages",
    )
    assert not decision.allowed
    assert "anthropic" in decision.reason and "not allowlisted" in decision.reason


def test_wrong_host_for_provider_is_denied():
    # An allowlist for openai must not authorize a different host.
    guard = _guard(acme=[OPENAI_EXACT])
    decision = guard.allow(
        tenant_id="acme",
        provider="openai",
        endpoint="https://api.anthropic.com/v1/chat/completions",
    )
    assert not decision.allowed


def test_path_prefix_is_enforced():
    guard = _guard(acme=[OPENAI_EXACT])  # only /v1/chat/completions allowed
    bad_path = guard.allow(
        tenant_id="acme", provider="openai", endpoint="https://api.openai.com/v1/images"
    )
    assert not bad_path.allowed


def test_subdomain_allowlist_allows_subdomains():
    guard = _guard(acme=[AllowedTarget("openai", ".openai.com")])
    assert guard.allow(
        tenant_id="acme", provider="openai", endpoint="https://api.openai.com/v1/chat"
    ).allowed
    assert guard.allow(
        tenant_id="acme", provider="openai", endpoint="https://chat.openai.com/"
    ).allowed


def test_suffix_squatting_host_is_denied():
    # api.openai.com.evil.example must NOT be authorized by api.openai.com or
    # by a .openai.com subdomain rule.
    guard_exact = _guard(acme=[OPENAI_EXACT])
    assert not guard_exact.allow(
        tenant_id="acme",
        provider="openai",
        endpoint="https://api.openai.com.evil.example/v1/chat/completions",
    ).allowed
    guard_sub = _guard(acme=[AllowedTarget("openai", ".openai.com")])
    assert not guard_sub.allow(
        tenant_id="acme",
        provider="openai",
        endpoint="https://api.openai.com.evil.example/v1/chat/completions",
    ).allowed


def test_tenant_isolation_no_cross_tenant_reach():
    guard = _guard(
        acme=[AllowedTarget("openai", "api.openai.com")],
        globex=[AllowedTarget("anthropic", "api.anthropic.com")],
    )
    # globex cannot reach acme's provider.
    assert not guard.allow(
        tenant_id="globex", provider="openai", endpoint="https://api.openai.com/v1"
    ).allowed
    # acme cannot reach globex's provider.
    assert not guard.allow(
        tenant_id="acme", provider="anthropic", endpoint="https://api.anthropic.com/v1"
    ).allowed


def test_host_match_is_case_insensitive():
    guard = _guard(acme=[AllowedTarget("openai", "api.openai.com")])
    assert guard.allow(
        tenant_id="acme",
        provider="openai",
        endpoint="https://API.OpenAI.COM/v1/chat/completions",
    ).allowed


def test_invalid_endpoint_url_is_denied():
    guard = _guard(acme=[OPENAI_EXACT])
    for bad in ("api.openai.com", "ftp://api.openai.com/x", "not a url"):
        decision = guard.allow(tenant_id="acme", provider="openai", endpoint=bad)
        assert not decision.allowed, f"expected deny for {bad!r}"


def test_targets_for_unknown_tenant_is_empty():
    guard = _guard(acme=[OPENAI_EXACT])
    assert guard.targets_for("nobody") == ()


def test_multi_provider_allowlist_each_independent():
    guard = _guard(
        acme=[
            AllowedTarget("openai", "api.openai.com", "/v1/chat/completions"),
            AllowedTarget("anthropic", "api.anthropic.com", "/v1/messages"),
        ]
    )
    assert guard.allow(
        tenant_id="acme", provider="openai", endpoint="https://api.openai.com/v1/chat/completions"
    ).allowed
    assert guard.allow(
        tenant_id="acme", provider="anthropic", endpoint="https://api.anthropic.com/v1/messages"
    ).allowed
    assert not guard.allow(
        tenant_id="acme", provider="openai", endpoint="https://api.anthropic.com/v1/messages"
    ).allowed
