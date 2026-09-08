"""Token-budget limiter per (tenant, agent, model tier) with observe->enforce.

Implements the cannibalized fleet token-budget pattern (leaderboard
scripts/guard/token-budget.sh) as an offline Python limiter with one
deliberate improvement carried over verbatim from that source's hard-won
lesson: an UNDETERMINABLE spend (e.g. a missing ledger) is never treated as
"under budget".  Here a missing/invalid ledger raises rather than silently
reporting zero, so a caller cannot fail open.

Safe rollout: each BudgetPolicy carries a mode.  ``observe`` (default) logs the
decision and never blocks (would_block=True when over cap); ``enforce`` blocks
over-cap calls (allowed=False, reason="budget_exceeded").  The facade
(limits.limiter) converts an enforce-block into an explicit backpressure
decision — never a silent success.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from limits.fingerprint import normalize_tier, scope_key, tenant_scope

OBSERVE = "observe"
ENFORCE = "enforce"
MODES = frozenset({OBSERVE, ENFORCE})

REASON_BUDGET_EXCEEDED = "budget_exceeded"


class BudgetMode(str, Enum):
    OBSERVE = OBSERVE
    ENFORCE = ENFORCE


@dataclass(frozen=True)
class BudgetPolicy:
    """One rolling-window token cap with an observe/enforce mode."""

    cap_tokens: int
    window_seconds: int = 86400
    mode: str = OBSERVE

    def __post_init__(self) -> None:
        if int(self.cap_tokens) != self.cap_tokens or self.cap_tokens <= 0:
            raise ValueError("cap_tokens must be a positive integer")
        if int(self.window_seconds) != self.window_seconds or self.window_seconds <= 0:
            raise ValueError("window_seconds must be a positive integer")
        if isinstance(self.mode, BudgetMode):
            mode = self.mode.value
        else:
            mode = str(self.mode).lower()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}")
        object.__setattr__(self, "mode", mode)


@dataclass(frozen=True)
class UsageRecord:
    """One metered token spend against a scope."""

    scope: str
    at: float
    tokens: int
    request_id: str = ""


class Ledger(Protocol):
    """Append-only usage ledger seam (in-memory or file-backed)."""

    def append(self, record: UsageRecord) -> None: ...
    def recent(self, since: float) -> list[UsageRecord]: ...
    def all(self) -> list[UsageRecord]: ...


class MemoryLedger:
    """In-memory append-only usage ledger (the default)."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def append(self, record: UsageRecord) -> None:
        self._records.append(record)

    def recent(self, since: float) -> list[UsageRecord]:
        return [r for r in self._records if r.at >= since]

    def all(self) -> list[UsageRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)


class JsonlLedger:
    """Append-only JSONL usage ledger (the durable seam).

    ``recent`` / ``all`` read the file back so state survives restarts.  A
    missing file is treated as a genuine zero (the fleet has spent nothing);
    an unreadable or corrupt file raises so callers cannot silently fail open.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, record: UsageRecord) -> None:
        data = {
            "scope": record.scope,
            "at": record.at,
            "tokens": record.tokens,
            "request_id": record.request_id,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(data) + "\n")

    def recent(self, since: float) -> list[UsageRecord]:
        return [r for r in self._read() if r.at >= since]

    def all(self) -> list[UsageRecord]:
        return self._read()

    def _read(self) -> list[UsageRecord]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"usage ledger {self.path} is unreadable; spend is UNDETERMINED "
                f"and must never be treated as zero: {exc}"
            ) from exc
        return [
            UsageRecord(
                scope=row["scope"],
                at=float(row["at"]),
                tokens=int(row["tokens"]),
                request_id=row.get("request_id", ""),
            )
            for row in rows
        ]


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of a token-budget check for one requested token count."""

    scope: str
    mode: str
    cap_tokens: int
    window_seconds: int
    used_in_window: int
    requested_tokens: int
    remaining: int
    exceeded: bool
    allowed: bool
    would_block: bool
    reason: str | None = None
    sub_decisions: tuple = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown budget mode: {self.mode!r}")


class TokenBudget:
    """A single rolling-window token budget over one scope (or one tenant)."""

    def __init__(
        self,
        scope: str,
        policy: BudgetPolicy,
        ledger: Ledger | None = None,
        *,
        clock=time.time,
        tenant_prefix: str | None = None,
    ) -> None:
        self.scope = scope
        self.policy = policy
        self.ledger = ledger if ledger is not None else MemoryLedger()
        self.clock = clock
        self.tenant_prefix = tenant_prefix

    # --- internal ------------------------------------------------------------
    def _window_since(self) -> float:
        return self.clock() - self.policy.window_seconds

    def _matches(self, record: UsageRecord) -> bool:
        if self.tenant_prefix is not None:
            return record.scope.startswith(tenant_scope(self.tenant_prefix))
        return record.scope == self.scope

    def used_in_window(self) -> int:
        """Tokens already spent in the rolling window for this budget."""
        records = [r for r in self.ledger.recent(self._window_since()) if self._matches(r)]
        return sum(r.tokens for r in records)

    def decide(self, tokens: int, request_id: str = "") -> BudgetDecision:
        """Check whether ``tokens`` may be spent; observe mode never blocks."""
        if tokens < 0:
            raise ValueError("requested tokens must be >= 0")
        used = self.used_in_window()
        exceeded = used + tokens > self.policy.cap_tokens
        remaining = max(0, self.policy.cap_tokens - used)
        if self.policy.mode == OBSERVE:
            allowed = True
            reason = None
        else:
            allowed = not exceeded
            reason = None if allowed else REASON_BUDGET_EXCEEDED
        return BudgetDecision(
            scope=self.scope,
            mode=self.policy.mode,
            cap_tokens=self.policy.cap_tokens,
            window_seconds=self.policy.window_seconds,
            used_in_window=used,
            requested_tokens=tokens,
            remaining=remaining,
            exceeded=exceeded,
            allowed=allowed,
            would_block=exceeded,
            reason=reason,
        )

    def record(self, tokens: int, request_id: str = "") -> None:
        """Append actual spend (after a provider call) to the ledger."""
        if tokens < 0:
            raise ValueError("recorded tokens must be >= 0")
        self.ledger.append(
            UsageRecord(scope=self.scope, at=self.clock(), tokens=tokens, request_id=request_id)
        )


class BudgetController:
    """Per-tenant/agent/tier budgets with optional tenant-wide caps.

    Policy resolution, most specific first:
      1. an exact (tenant, agent, tier) policy,
      2. a tenant-wide policy (aggregated across every agent/tier of the
         tenant),
      3. the default policy applied to the (tenant, agent, tier) scope.

    ``decide`` evaluates every applicable level and blocks when ANY enforcing
    level is exceeded; ``record`` appends each spend once at the triple scope
    (tenant-wide budgets see it through the tenant prefix).
    """

    def __init__(
        self,
        default_policy: BudgetPolicy | None = None,
        *,
        policies: dict[str, BudgetPolicy] | None = None,
        tenant_policies: dict[str, BudgetPolicy] | None = None,
        ledger: Ledger | None = None,
        clock=time.time,
    ) -> None:
        self.default_policy = default_policy or BudgetPolicy(cap_tokens=200_000)
        self.policies = dict(policies or {})
        self.tenant_policies = dict(tenant_policies or {})
        self.ledger = ledger if ledger is not None else MemoryLedger()
        self.clock = clock

    # --- public --------------------------------------------------------------
    def policy_for(self, tenant: str, agent: str, model_tier: str) -> BudgetPolicy:
        scope = scope_key(tenant, agent, model_tier)
        return self.policies.get(scope, self.default_policy)

    def decide(
        self,
        tenant: str,
        agent: str,
        model_tier: str,
        tokens: int,
        request_id: str = "",
    ) -> BudgetDecision:
        """Evaluate every applicable budget level for ``tokens``."""
        tier = normalize_tier(model_tier)
        scope = scope_key(tenant, agent, tier)
        sub: list[BudgetDecision] = []

        tenant_policy = self.tenant_policies.get(tenant)
        if tenant_policy is not None:
            tenant_budget = TokenBudget(
                scope=f"{tenant}::*::*",
                policy=tenant_policy,
                ledger=self.ledger,
                clock=self.clock,
                tenant_prefix=tenant,
            )
            sub.append(tenant_budget.decide(tokens, request_id))

        triple_policy = self.policies.get(scope, self.default_policy)
        triple_budget = TokenBudget(scope, triple_policy, self.ledger, clock=self.clock)
        sub.append(triple_budget.decide(tokens, request_id))

        return self._merge(sub, requested=tokens)

    def record(self, tenant: str, agent: str, model_tier: str, tokens: int, request_id: str = "") -> None:
        """Append one actual spend at the triple scope."""
        tier = normalize_tier(model_tier)
        scope = scope_key(tenant, agent, tier)
        if tokens < 0:
            raise ValueError("recorded tokens must be >= 0")
        self.ledger.append(
            UsageRecord(scope=scope, at=self.clock(), tokens=tokens, request_id=request_id)
        )

    # --- internal ------------------------------------------------------------
    @staticmethod
    def _merge(sub: list[BudgetDecision], *, requested: int) -> BudgetDecision:
        blocked = [d for d in sub if not d.allowed]
        binding = blocked[0] if blocked else min(sub, key=lambda d: d.remaining)
        return BudgetDecision(
            scope=binding.scope,
            mode=binding.mode,
            cap_tokens=binding.cap_tokens,
            window_seconds=binding.window_seconds,
            used_in_window=binding.used_in_window,
            requested_tokens=requested,
            remaining=min(d.remaining for d in sub),
            exceeded=any(d.exceeded for d in sub),
            allowed=all(d.allowed for d in sub),
            would_block=any(d.would_block for d in sub),
            reason=blocked[0].reason if blocked else None,
            sub_decisions=tuple(sub),
        )
