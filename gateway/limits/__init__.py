"""gateway/limits — cost/capacity control layer (issue #19).

Public surface
--------------

- ``LimitsEngine`` (limits.limiter) - the contract the model-gateway proxy
  consumes: ``guard()`` then ``complete()`` (or ``execute()`` for a provider
  callable).  ``build_engine()`` wires one from config/limits.yaml.
- ``SemanticCache`` (limits.cache) - prompt-fingerprint dedup with TTL; a hit
  is zero-cost and accounted (metering outcome=cache_hit, zero_cost=True).
- ``BudgetController`` / ``TokenBudget`` (limits.budget) - per
  (tenant, agent, model tier) rolling-window token budget with an
  observe->enforce toggle.
- ``RateLimiter`` (limits.ratelimit) - token-bucket rate limiter per scope.
- ``OutputThrottle`` (limits.throttle) - per-taskType output token caps
  (trim/refuse) to stop runaway loops.
- ``BackpressureController`` (limits.backpressure) - explicit queue/degrade
  decision when a budget is exhausted; never a silent success.
- ``fingerprint`` (limits.fingerprint) - prompt normalization + sha256 keys
  bound to (tenant, model tier, prompt, taskType).
- ``model`` (limits.model) - ModelCallRequest + MeteringRecord (the
  accounting record every outcome writes).
- ``config`` (limits.config) - typed YAML configuration + component builders.

The package is importable as ``limits`` when ``gateway/`` is on ``sys.path``
(the tests arrange this in ``tests/conftest.py``; consumers of the merged
contract should do the same or run the CLI directly).
"""

from limits.backpressure import (
    BackpressureController,
    BackpressureDecision,
    BackpressureQueue,
    QueueJob,
)
from limits.budget import (
    BudgetController,
    BudgetDecision,
    BudgetMode,
    BudgetPolicy,
    TokenBudget,
)
from limits.cache import CacheEntry, FileCacheStore, MemoryCacheStore, SemanticCache
from limits.fingerprint import cache_key, prompt_fingerprint, scope_key
from limits.limiter import (
    KIND_ALLOW,
    KIND_BUDGET_EXCEEDED,
    KIND_CACHE_HIT,
    KIND_RATE_LIMITED,
    CompleteResult,
    GuardDecision,
    LimitsEngine,
    build_engine,
)
from limits.model import (
    CACHE_HIT,
    DEGRADED,
    PROVIDER,
    QUEUED,
    RATE_LIMITED,
    REFUSED,
    MeteringRecord,
    ModelCallRequest,
)
from limits.ratelimit import RateLimitDecision, RateLimitPolicy, RateLimiter, TokenBucket
from limits.throttle import OutputThrottle, ThrottleVerdict

__all__ = [
    "BackpressureController",
    "BackpressureDecision",
    "BackpressureQueue",
    "BudgetController",
    "BudgetDecision",
    "BudgetMode",
    "BudgetPolicy",
    "CACHE_HIT",
    "CacheEntry",
    "CompleteResult",
    "DEGRADED",
    "FileCacheStore",
    "GuardDecision",
    "KIND_ALLOW",
    "KIND_BUDGET_EXCEEDED",
    "KIND_CACHE_HIT",
    "KIND_RATE_LIMITED",
    "LimitsEngine",
    "MemoryCacheStore",
    "MeteringRecord",
    "ModelCallRequest",
    "OutputThrottle",
    "PROVIDER",
    "QUEUED",
    "QueueJob",
    "RATE_LIMITED",
    "RateLimitDecision",
    "RateLimitPolicy",
    "RateLimiter",
    "REFUSED",
    "SemanticCache",
    "ThrottleVerdict",
    "TokenBucket",
    "TokenBudget",
    "build_engine",
    "cache_key",
    "prompt_fingerprint",
    "scope_key",
]
