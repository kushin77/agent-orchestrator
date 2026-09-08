"""YAML configuration for the cost/capacity control layer (gateway/limits).

Loads config/limits.yaml (or an override file with the same shape) into typed
dataclasses and exposes component builders so the facade (limits.limiter) and
the CLI wire a fully configured LimitsEngine offline.  Everything here is
configuration data; no secrets ever live in these files (repo GR-6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from limits.backpressure import BackpressureController
from limits.budget import BudgetController, BudgetPolicy
from limits.cache import FileCacheStore, MemoryCacheStore, SemanticCache
from limits.ratelimit import RateLimiter, RateLimitPolicy
from limits.throttle import DEFAULT_CAPS, OutputThrottle

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "limits.yaml"


def default_config_path() -> Path:
    return DEFAULT_CONFIG_PATH


# --- typed configuration ----------------------------------------------------
@dataclass
class CacheConfig:
    enabled: bool = True
    store: str = "memory"  # memory | file
    file_dir: str = ""
    default_ttl_seconds: float = 3600.0
    max_entries: int = 10_000
    lowercase: bool = False
    strip_punctuation: bool = False


@dataclass
class BudgetConfig:
    default_cap_tokens: int = 200_000
    default_window_seconds: int = 86_400
    default_mode: str = "observe"
    scope_overrides: dict[str, dict] = field(default_factory=dict)
    tenant_overrides: dict[str, dict] = field(default_factory=dict)


@dataclass
class RateConfig:
    default_limit: int = 100
    default_window_seconds: float = 60.0
    default_burst: int = 20
    scope_overrides: dict[str, dict] = field(default_factory=dict)


@dataclass
class ThrottleConfig:
    default_cap: int = 2000
    mode: str = "trim"
    chars_per_token: int = 4
    caps: dict[str, int] = field(default_factory=dict)


@dataclass
class BackpressureConfig:
    strategy: str = "degrade"  # degrade | queue
    queue_capacity: int = 1000


@dataclass
class LimitsConfig:
    cache: CacheConfig = field(default_factory=CacheConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    rate: RateConfig = field(default_factory=RateConfig)
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)
    backpressure: BackpressureConfig = field(default_factory=BackpressureConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LimitsConfig":
        cache = data.get("cache") or {}
        budget = data.get("budget") or {}
        rate = data.get("rate") or {}
        throttle = data.get("throttle") or {}
        backpressure = data.get("backpressure") or {}
        return cls(
            cache=CacheConfig(
                enabled=bool(cache.get("enabled", True)),
                store=str(cache.get("store", "memory")),
                file_dir=str(cache.get("file_dir", "")),
                default_ttl_seconds=float(cache.get("default_ttl_seconds", 3600)),
                max_entries=int(cache.get("max_entries", 10_000)),
                lowercase=bool(cache.get("lowercase", False)),
                strip_punctuation=bool(cache.get("strip_punctuation", False)),
            ),
            budget=BudgetConfig(
                default_cap_tokens=int(budget.get("default_cap_tokens", 200_000)),
                default_window_seconds=int(budget.get("default_window_seconds", 86_400)),
                default_mode=str(budget.get("default_mode", "observe")),
                scope_overrides=dict(budget.get("scope_overrides") or {}),
                tenant_overrides=dict(budget.get("tenant_overrides") or {}),
            ),
            rate=RateConfig(
                default_limit=int(rate.get("default_limit", 100)),
                default_window_seconds=float(rate.get("default_window_seconds", 60)),
                default_burst=int(rate.get("default_burst", 20)),
                scope_overrides=dict(rate.get("scope_overrides") or {}),
            ),
            throttle=ThrottleConfig(
                default_cap=int(throttle.get("default_cap", 2000)),
                mode=str(throttle.get("mode", "trim")),
                chars_per_token=int(throttle.get("chars_per_token", 4)),
                caps={str(k): int(v) for k, v in (throttle.get("caps") or {}).items()},
            ),
            backpressure=BackpressureConfig(
                strategy=str(backpressure.get("strategy", "degrade")),
                queue_capacity=int(backpressure.get("queue_capacity", 1000)),
            ),
        )


def load_config(path: str | Path | None = None) -> LimitsConfig:
    """Load config from ``path`` (default config/limits.yaml) into dataclasses.

    A missing override path falls back to the committed default file; a missing
    default file yields built-in defaults (config remains fully optional).
    """
    cfg_path = Path(path) if path else default_config_path()
    if cfg_path.is_file():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        data = {}
    return LimitsConfig.from_dict(data)


# --- component builders -----------------------------------------------------
def build_cache(cfg: LimitsConfig | None = None) -> SemanticCache:
    cfg = cfg or load_config()
    c = cfg.cache
    if c.store == "file":
        if not c.file_dir:
            raise ValueError("cache.store=file requires cache.file_dir")
        store: MemoryCacheStore | FileCacheStore = FileCacheStore(c.file_dir)
    else:
        store = MemoryCacheStore()
    return SemanticCache(
        store=store,
        default_ttl=c.default_ttl_seconds,
        max_entries=c.max_entries,
        lowercase=c.lowercase,
        strip_punctuation=c.strip_punctuation,
    )


def _budget_policy_from(spec: dict, cfg: BudgetConfig) -> BudgetPolicy:
    return BudgetPolicy(
        cap_tokens=int(spec.get("cap_tokens", cfg.default_cap_tokens)),
        window_seconds=int(spec.get("window_seconds", cfg.default_window_seconds)),
        mode=str(spec.get("mode", cfg.default_mode)),
    )


def build_budget(cfg: LimitsConfig | None = None) -> BudgetController:
    cfg = cfg or load_config()
    b = cfg.budget
    policies = {scope: _budget_policy_from(spec, b) for scope, spec in b.scope_overrides.items()}
    tenant_policies = {
        tenant: _budget_policy_from(spec, b) for tenant, spec in b.tenant_overrides.items()
    }
    return BudgetController(
        default_policy=BudgetPolicy(
            cap_tokens=b.default_cap_tokens,
            window_seconds=b.default_window_seconds,
            mode=b.default_mode,
        ),
        policies=policies,
        tenant_policies=tenant_policies,
    )


def _rate_policy_from(spec: dict, cfg: RateConfig) -> RateLimitPolicy:
    return RateLimitPolicy(
        limit=int(spec.get("limit", cfg.default_limit)),
        window_seconds=float(spec.get("window_seconds", cfg.default_window_seconds)),
        burst=int(spec.get("burst", cfg.default_burst)),
    )


def build_rate(cfg: LimitsConfig | None = None) -> RateLimiter:
    cfg = cfg or load_config()
    r = cfg.rate
    policies = {scope: _rate_policy_from(spec, r) for scope, spec in r.scope_overrides.items()}
    return RateLimiter(
        default_policy=RateLimitPolicy(
            limit=r.default_limit,
            window_seconds=r.default_window_seconds,
            burst=r.default_burst,
        ),
        policies=policies,
    )


def build_throttle(cfg: LimitsConfig | None = None) -> OutputThrottle:
    cfg = cfg or load_config()
    t = cfg.throttle
    caps = dict(DEFAULT_CAPS)
    caps.update(t.caps)  # file caps override the built-in taxonomy
    return OutputThrottle(
        caps=caps,
        default_cap=t.default_cap,
        mode=t.mode,
        chars_per_token=t.chars_per_token,
    )


def build_backpressure(cfg: LimitsConfig | None = None) -> BackpressureController:
    cfg = cfg or load_config()
    bp = cfg.backpressure
    return BackpressureController(strategy=bp.strategy)
