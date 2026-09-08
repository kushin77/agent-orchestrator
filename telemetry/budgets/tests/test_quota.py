"""telemetry/budgets — quota enforcer tests (issue #34).

Covers soft/hard enforcement over calls/tokens/concurrency/storage, the
negative (hard quota exceeded -> refused), plan-default resolution and
per-tenant overrides (the entitlements/plan link).
"""

from __future__ import annotations

import pytest

from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WARN,
    OUTCOME_BLOCKED,
    RESOURCE_CONCURRENCY,
    RESOURCE_REQUESTS,
    RESOURCE_STORAGE,
    RESOURCE_TOKENS,
    QUOTA_STATUS_EXCEEDED,
    QUOTA_STATUS_OK,
    QUOTA_STATUS_WARNING,
)
from telemetry.budgets.quota import (
    QuotaEnforcer,
    QuotaLimit,
    QuotaPolicy,
    StaticProbe,
    load_quota_policies,
)

DAY = "2026-09-08"


def _qpolicy(tenant: str, plan: str = "free", **limits) -> QuotaPolicy:
    return QuotaPolicy(
        tenant_id=tenant,
        plan=plan,
        limits={
            name: QuotaLimit(resource=name, window=kw.get("window"),
                             soft_limit=kw["soft"], hard_limit=kw["hard"])
            for name, kw in limits.items()
        },
    )


def _enforcer(ledger: StaticLedger, *policies, probe=None):
    return QuotaEnforcer(ledger, {p.tenant_id: p for p in policies}, probe=probe)


def _requests(soft: float, hard: float) -> dict:
    return {"window": "day", "soft": soft, "hard": hard}


# --------------------------------------------------------------------------- #
# requests (calls) quota
# --------------------------------------------------------------------------- #
def test_requests_within_soft_allows():
    ledger = StaticLedger()
    ledger.seed_calls("acme", DAY, 50)
    enforcer = _enforcer(ledger, _qpolicy("acme", requests=_requests(100, 500)))
    d = enforcer.check("acme", resource=RESOURCE_REQUESTS, requested=1, day=DAY)
    assert d.decision == DECISION_ALLOW


def test_requests_over_hard_is_blocked():
    ledger = StaticLedger()
    ledger.seed_calls("acme", DAY, 500)  # hard 500; +1 -> blocked
    enforcer = _enforcer(ledger, _qpolicy("acme", requests=_requests(100, 500)))
    d = enforcer.check("acme", resource=RESOURCE_REQUESTS, requested=1, day=DAY)
    assert d.decision == DECISION_BLOCK
    assert not d.allowed
    assert d.code == "quota.hard.requests.exceeded"
    assert d.outcome == OUTCOME_BLOCKED


def test_requests_over_soft_but_under_hard_warns():
    ledger = StaticLedger()
    ledger.seed_calls("acme", DAY, 150)
    enforcer = _enforcer(ledger, _qpolicy("acme", requests=_requests(100, 500)))
    d = enforcer.check("acme", resource=RESOURCE_REQUESTS, requested=1, day=DAY)
    assert d.decision == DECISION_WARN
    assert d.allowed  # soft only


# --------------------------------------------------------------------------- #
# tokens quota
# --------------------------------------------------------------------------- #
def test_tokens_over_hard_is_blocked():
    ledger = StaticLedger()
    ledger.seed_tokens("acme", DAY, 480_000)
    enforcer = _enforcer(
        ledger, _qpolicy("acme", tokens=_requests(100_000, 500_000))
    )
    d = enforcer.check("acme", resource=RESOURCE_TOKENS,
                       requested=30_000, day=DAY)  # 510k > 500k
    assert d.decision == DECISION_BLOCK
    assert d.code == "quota.hard.tokens.exceeded"


# --------------------------------------------------------------------------- #
# concurrency quota (live probe)
# --------------------------------------------------------------------------- #
def test_concurrency_at_hard_is_blocked():
    probe = StaticProbe()
    probe.seed_inflight("acme", 20)  # hard 20; this call -> 21
    enforcer = _enforcer(
        StaticLedger(), _qpolicy("acme", concurrency={"soft": 8, "hard": 20}),
        probe=probe,
    )
    d = enforcer.check("acme", resource=RESOURCE_CONCURRENCY, requested=1)
    assert d.decision == DECISION_BLOCK
    assert d.code == "quota.hard.concurrency.exceeded"


def test_concurrency_within_hard_allows():
    probe = StaticProbe()
    probe.seed_inflight("acme", 7)
    enforcer = _enforcer(
        StaticLedger(), _qpolicy("acme", concurrency={"soft": 8, "hard": 20}),
        probe=probe,
    )
    d = enforcer.check("acme", resource=RESOURCE_CONCURRENCY, requested=1)
    assert d.decision == DECISION_ALLOW


# --------------------------------------------------------------------------- #
# storage quota
# --------------------------------------------------------------------------- #
def test_storage_over_hard_is_blocked():
    probe = StaticProbe()
    probe.seed_storage("acme", 5_500_000_000)  # hard 5 GiB -> over with delta
    enforcer = _enforcer(
        StaticLedger(),
        _qpolicy("acme", storage={"soft": 1_000_000_000, "hard": 5_000_000_000}),
        probe=probe,
    )
    d = enforcer.check("acme", resource=RESOURCE_STORAGE,
                       requested=500_000_000)
    assert d.decision == DECISION_BLOCK
    assert d.code == "quota.hard.storage.exceeded"


# --------------------------------------------------------------------------- #
# status ladder + all-resources check
# --------------------------------------------------------------------------- #
def test_status_ladder():
    policy = _qpolicy("acme", requests=_requests(100, 500))
    ledger = StaticLedger()
    enforcer = _enforcer(ledger, policy)
    assert enforcer.status("acme", RESOURCE_REQUESTS, day=DAY) == QUOTA_STATUS_OK
    ledger.seed_calls("acme", DAY, 100)
    assert enforcer.status("acme", RESOURCE_REQUESTS, day=DAY) == QUOTA_STATUS_WARNING
    ledger.seed_calls("acme", DAY, 500)
    assert enforcer.status("acme", RESOURCE_REQUESTS, day=DAY) == QUOTA_STATUS_EXCEEDED


def test_all_resources_most_severe_wins():
    ledger = StaticLedger()
    ledger.seed_calls("acme", DAY, 600)  # over hard requests
    ledger.seed_tokens("acme", DAY, 10_000)  # under token soft 100k
    enforcer = _enforcer(
        ledger,
        _qpolicy(
            "acme",
            requests=_requests(100, 500),
            tokens=_requests(100_000, 500_000),
        ),
    )
    d = enforcer.check("acme", day=DAY)  # no resource -> all, most severe
    assert d.decision == DECISION_BLOCK
    assert d.code == "quota.hard.requests.exceeded"


# --------------------------------------------------------------------------- #
# Plan defaults + per-tenant overrides (entitlements link, phase 6)
# --------------------------------------------------------------------------- #
def test_plan_defaults_resolve():
    policies = load_quota_policies()
    globex = policies["globex"]  # plan standard, no explicit override
    assert globex.plan == "standard"
    req = globex.limit_for(RESOURCE_REQUESTS)
    assert req is not None
    assert req.hard_limit == 5000  # standard plan default


def test_tenant_override_beats_plan_default():
    policies = load_quota_policies()
    acme = policies["acme"]  # enterprise plan, requests override to hard 20000
    assert acme.plan == "enterprise"
    req = acme.limit_for(RESOURCE_REQUESTS)
    assert req.hard_limit == 20000          # explicit override wins
    conc = acme.limit_for(RESOURCE_CONCURRENCY)
    assert conc.hard_limit == 60            # explicit override wins
    tok = acme.limit_for(RESOURCE_TOKENS)
    assert tok.hard_limit == 50_000_000     # enterprise plan default retained


def test_unconfigured_tenant_allows():
    enforcer = _enforcer(StaticLedger())
    d = enforcer.check("ghost", resource=RESOURCE_REQUESTS)
    assert d.decision == DECISION_ALLOW
    assert d.code == "quota.unconfigured"


def test_invalid_soft_over_hard_rejected():
    with pytest.raises(ValueError):
        QuotaLimit(resource=RESOURCE_REQUESTS, window="day",
                   soft_limit=500, hard_limit=100)
