"""telemetry/chat — prompt-cache accounting for one chat turn (issue #506).

Prompt/prefix caching is a *provider* feature (Anthropic ``cache_control``,
DeepSeek automatic prefix caching): the stable system + memory prefix is
billed once and then served from cache, so a turn that reuses a prefix costs
strictly less than the same turn cold.  This module reports that footprint for
one chat turn:

- ``account_prefix(static_prefix, user_delta)`` — the *shape* of the prompt,
  a pure function of the text: how many tokens are in the cacheable prefix,
  how many in the uncacheable delta, and whether the prefix is cacheable at
  all.  Cacheability is decided by the **consumed**
  ``engine/memory/prompt_cache`` discipline (issue #25): a prefix whose bytes
  vary per run (a run id, a timestamp, a session id) re-fills the cache on
  every call, so it is accounted as *uncacheable* rather than silently
  credited with a hit.
- ``CacheAccounting`` — the per-turn observation: the prefix shape plus how
  many prefix tokens the provider actually served from cache.

This lane computes **no token counts of its own** where the consumed
estimator exists (``engine.memory.prompt_cache.estimate_tokens``), and it
computes **no price at all** — cost is resolved by ``attribution`` from the
``telemetry/metering`` rate cards.  Accounting only decides how many input
tokens are *billable* (the uncached remainder); the rate card prices them.

The reachable invariant: a provider can never report more cached tokens than
the turn's cacheable prefix holds.  A figure above that is impossible (the
cache cannot serve tokens the prompt does not contain), so it is refused
loudly instead of quietly discounting a turn below its real cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from engine.memory.prompt_cache import (
    PrefixError,
    estimate_tokens,
    footprint,
    scan_dynamic,
    validate_static_region,
)

#: Per-turn prefix-reuse classification.
KIND_COLD = "cold"      # no prefix token was served from cache
KIND_PARTIAL = "partial"  # some of the cacheable prefix was reused
KIND_FULL = "full"      # the whole cacheable prefix was reused

KINDS = frozenset({KIND_COLD, KIND_PARTIAL, KIND_FULL})


class CacheAccountingError(ValueError):
    """The reported cache usage is impossible for this turn's prompt."""


@dataclass(frozen=True)
class PrefixAccounting:
    """The cacheable-prefix shape of one turn's prompt (pure function of text).

    ``cacheable`` is ``False`` when the static region carries dynamic tokens
    (the ``engine/memory/prompt_cache`` rejection, consumed): such a prefix
    cannot be reused byte-for-byte, so ``max_cacheable_tokens`` is ``0`` and
    ``reason`` names what killed it.  ``fingerprint`` is the sha256 of the
    static region (the consumed ``footprint`` helper) — two turns whose
    fingerprints differ presented a different prefix, so neither can have hit
    the other's cache.
    """

    prompt_tokens: int
    static_tokens: int
    delta_tokens: int
    cacheable: bool
    fingerprint: str
    dynamic_tokens: Tuple[str, ...] = ()
    reason: Optional[str] = None

    @property
    def max_cacheable_tokens(self) -> int:
        """The most prefix tokens a provider could serve from cache."""
        return self.static_tokens if self.cacheable else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promptTokens": self.prompt_tokens,
            "staticTokens": self.static_tokens,
            "deltaTokens": self.delta_tokens,
            "cacheable": self.cacheable,
            "maxCacheableTokens": self.max_cacheable_tokens,
            "fingerprint": self.fingerprint,
            "dynamicTokens": list(self.dynamic_tokens),
            "reason": self.reason,
        }


def account_prefix(static_prefix: str = "", user_delta: str = "") -> PrefixAccounting:
    """Account one prompt's cacheable prefix and uncacheable delta.

    Token counts are the consumed ``estimate_tokens`` estimate of each region
    separately, so ``prompt_tokens == static_tokens + delta_tokens`` exactly
    (the provider bills the prefix and the delta as distinct regions).  A
    static region carrying dynamic tokens is reported ``cacheable=False``
    with the offending tokens named — never silently stripped, which would
    misreport the bytes actually sent.
    """
    static_text = (static_prefix or "").strip()
    delta_text = (user_delta or "").strip()
    static_tokens = estimate_tokens(static_text)
    delta_tokens = estimate_tokens(delta_text)

    dynamic: Tuple[str, ...] = ()
    reason: Optional[str] = None
    cacheable = True
    if static_text:
        try:
            validate_static_region(static_text)
        except PrefixError as exc:
            cacheable = False
            reason = str(exc)
            dynamic = tuple(sorted(set(scan_dynamic(static_text))))

    return PrefixAccounting(
        prompt_tokens=static_tokens + delta_tokens,
        static_tokens=static_tokens,
        delta_tokens=delta_tokens,
        cacheable=cacheable,
        fingerprint=footprint(static_text),
        dynamic_tokens=dynamic,
        reason=reason,
    )


@dataclass(frozen=True)
class CacheAccounting:
    """One turn's prompt-cache footprint and observed prefix reuse.

    ``cached_tokens`` is the number of *prefix* tokens the provider served
    from cache (observed and reported by the chat surface; the gateway call
    record carries no cache field).  Absent an observation the caller passes
    ``0`` — accounted cold, which never under-reports the turn's cost.  The
    billable input is the uncached remainder
    (``prefix.prompt_tokens - cached_tokens``); the rate card prices it.
    """

    prefix: PrefixAccounting
    cached_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if self.cached_tokens < 0:
            raise CacheAccountingError(
                f"cached_tokens must be >= 0, got {self.cached_tokens}"
            )
        ceiling = self.prefix.max_cacheable_tokens
        if self.cached_tokens > ceiling:
            raise CacheAccountingError(
                "impossible cache report: "
                f"{self.cached_tokens} cached tokens > {ceiling} cacheable "
                f"prefix tokens ({self.prefix.reason or 'prefix is cacheable'})"
            )
        if self.output_tokens < 0:
            raise CacheAccountingError(
                f"output_tokens must be >= 0, got {self.output_tokens}"
            )

    @property
    def prompt_tokens(self) -> int:
        return self.prefix.prompt_tokens

    @property
    def cacheable(self) -> bool:
        return self.prefix.cacheable

    @property
    def hit_share(self) -> float:
        """Fraction of the turn's prompt tokens served from cache (0.0-1.0)."""
        if self.prompt_tokens <= 0:
            return 0.0
        return round(self.cached_tokens / self.prompt_tokens, 6)

    @property
    def hit_share_of_prefix(self) -> float:
        """Fraction of the *cacheable prefix* that was reused (0.0-1.0)."""
        ceiling = self.prefix.max_cacheable_tokens
        if ceiling <= 0:
            return 0.0
        return round(self.cached_tokens / ceiling, 6)

    @property
    def billable_input_tokens(self) -> int:
        """Input tokens the provider will bill (the uncached remainder)."""
        return max(0, self.prompt_tokens - self.cached_tokens)

    @property
    def kind(self) -> str:
        if self.cached_tokens <= 0:
            return KIND_COLD
        if self.cached_tokens >= self.prompt_tokens:
            return KIND_FULL
        return KIND_PARTIAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "prefix": self.prefix.to_dict(),
            "promptTokens": self.prompt_tokens,
            "cachedTokens": self.cached_tokens,
            "outputTokens": self.output_tokens,
            "billableInputTokens": self.billable_input_tokens,
            "cacheHitShare": self.hit_share,
            "cacheHitShareOfPrefix": self.hit_share_of_prefix,
        }
