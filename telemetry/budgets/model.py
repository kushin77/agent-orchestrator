"""telemetry/budgets — decision/resource/window vocabulary + value objects (issue #34).

This lane is the per-tenant budgets + quotas + global kill-switch contract of
the telemetry pillar.  Vocabulary is CONSUMED from the merged sibling lanes
and never redefined:

- the decision ladder ``allow -> warn -> (fallback) -> block`` and the
  ``warnAtPct``/``hardCapPct`` thresholds come from the gateway/finops budget
  enforcer (issue #17, ``stop|warn|fallback`` policies);
- the non-billable outcome strings this lane's refusals map onto
  (``budget_exceeded`` / ``blocked`` / ``refused``) come from the metering
  intake (issue #33, ``NON_BILLABLE_OUTCOMES``) so a refused call is never
  metered as usage;
- the spend feed itself (durable daily/monthly totals per tenant) is the
  metering store the issue-#33 lane owns — read through ``ledger.py``, never
  re-implemented;
- the SLO verdict vocabulary (``OK`` / ``AT_RISK`` / ``BREACHED`` /
  ``NO_DATA``) consumed by the exporter comes from telemetry/observability
  (issue #32, ``telemetry/observability/slos.py``);
- the Org-as-tenant model comes from identity/rbac (issue #12).

Everything here is pure data (no I/O, no network).  Writers/readers live in
the enforcer modules; the durable audit store lives in ``audit.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Enforcer decision ladder (consumed from gateway/finops, issue #17)
# --------------------------------------------------------------------------- #
DECISION_ALLOW = "allow"        # within budget/quota — call may proceed
DECISION_WARN = "warn"          # at/above warn threshold — flagged, still allowed
DECISION_FALLBACK = "fallback"  # may downgrade tier instead of blocking (budget ladder)
DECISION_WOULD_WARN = "would_warn"    # observe mode: would warn if enforcing
DECISION_WOULD_BLOCK = "would_block"  # observe mode: would block if enforcing
DECISION_BLOCK = "block"        # hard stop — the caller must refuse the call
DECISION_REFUSE = "refuse"      # kill switch — call refused regardless of budget/quota

#: Decisions that let the caller proceed (only ``block``/``refuse`` refuse).
ALLOWED_DECISIONS = frozenset(
    {
        DECISION_ALLOW,
        DECISION_WARN,
        DECISION_FALLBACK,
        DECISION_WOULD_WARN,
        DECISION_WOULD_BLOCK,
    }
)
BLOCKING_DECISIONS = frozenset({DECISION_BLOCK, DECISION_REFUSE})
DECISIONS = ALLOWED_DECISIONS | BLOCKING_DECISIONS

#: Outcomes this lane's refusals map onto (CONSUMED from metering #33
#: ``NON_BILLABLE_OUTCOMES``) — a refused call is never metered as usage.
OUTCOME_BUDGET_EXCEEDED = "budget_exceeded"
OUTCOME_BLOCKED = "blocked"
OUTCOME_REFUSED = "refused"

#: Enforcer kinds (which rail made the decision) — used in audit + export.
KIND_BUDGET = "budget"
KIND_QUOTA = "quota"
KIND_KILL_SWITCH = "kill_switch"

# --------------------------------------------------------------------------- #
# Budget rollout modes (consumed from metering #33 observe/enforce toggle)
# --------------------------------------------------------------------------- #
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
MODES = frozenset({MODE_OBSERVE, MODE_ENFORCE})

# --------------------------------------------------------------------------- #
# Quota resources (issue #34 acceptance: calls, tokens, concurrency, storage)
# --------------------------------------------------------------------------- #
RESOURCE_REQUESTS = "requests"        # calls dispatched per window
RESOURCE_TOKENS = "tokens"            # tokens consumed per window
RESOURCE_CONCURRENCY = "concurrency"  # in-flight model calls
RESOURCE_STORAGE = "storage"          # tenant storage (bytes)
QUOTA_RESOURCES = frozenset(
    {RESOURCE_REQUESTS, RESOURCE_TOKENS, RESOURCE_CONCURRENCY, RESOURCE_STORAGE}
)

#: Quota status ladder (consumed from shared-services quota enforcer):
#: ok < warning < critical < exceeded (hard limit hit -> deny).
QUOTA_STATUS_OK = "ok"
QUOTA_STATUS_WARNING = "warning"
QUOTA_STATUS_CRITICAL = "critical"
QUOTA_STATUS_EXCEEDED = "exceeded"
QUOTA_STATUSES = frozenset(
    {
        QUOTA_STATUS_OK,
        QUOTA_STATUS_WARNING,
        QUOTA_STATUS_CRITICAL,
        QUOTA_STATUS_EXCEEDED,
    }
)

# --------------------------------------------------------------------------- #
# Budget/quota windows
# --------------------------------------------------------------------------- #
WINDOW_DAY = "day"
WINDOW_MONTH = "month"
WINDOWS = frozenset({WINDOW_DAY, WINDOW_MONTH})

# --------------------------------------------------------------------------- #
# Time helpers (mirror the metering/observability RFC 3339 ``Z`` shape)
# --------------------------------------------------------------------------- #
def now_utc_iso() -> str:
    """UTC timestamp in the repo-wide RFC 3339 ``Z`` shape."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_bucket(ts: str) -> str:
    """UTC calendar-day bucket (``YYYY-MM-DD``) for a record timestamp."""
    return ts[:10]


def month_bucket(ts: str) -> str:
    """UTC calendar-month bucket (``YYYY-MM``) for a record timestamp."""
    return ts[:7]


def today_utc() -> str:
    """Today's UTC ``YYYY-MM-DD`` bucket."""
    return now_utc_iso()[:10]


def this_month_utc() -> str:
    """This UTC month's ``YYYY-MM`` bucket."""
    return now_utc_iso()[:7]


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EnforcerDecision:
    """The result of one pre-flight enforcement check (budget/quota/kill).

    ``decision`` follows the ladder above; ``code`` is a stable machine
    reason (e.g. ``budget.hard.monthly_cost.exceeded``,
    ``quota.hard.concurrency.exceeded``, ``kill_switch.global_pause``) that
    alerting and the audit feed can key on.  ``outcome`` is the metering
    non-billable outcome the caller should attach if the call is refused.
    """

    tenant_id: str
    kind: str
    decision: str
    reason: str
    code: str
    agent_id: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    resource: Optional[str] = None
    window: Optional[str] = None
    current: Optional[float] = None
    requested: Optional[float] = None
    limit: Optional[float] = None
    warn_at: Optional[float] = None
    mode: Optional[str] = None
    outcome: Optional[str] = None
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"unknown decision: {self.decision!r}")
        if self.kind not in {KIND_BUDGET, KIND_QUOTA, KIND_KILL_SWITCH}:
            raise ValueError(f"unknown enforcer kind: {self.kind!r}")

    @property
    def allowed(self) -> bool:
        """True when the caller may proceed (only block/refuse refuse)."""
        return self.decision in ALLOWED_DECISIONS

    @property
    def auditable(self) -> bool:
        """True when this decision should be recorded to the audit feed.

        BLOCK/WARN decisions (and their observe-mode would-* equivalents)
        are auditable; a plain allow carries no signal.
        """
        return self.decision in {
            DECISION_WARN,
            DECISION_FALLBACK,
            DECISION_WOULD_WARN,
            DECISION_WOULD_BLOCK,
            DECISION_BLOCK,
            DECISION_REFUSE,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the camelCase machine-readable shape."""
        return {
            "decisionId": self.decision_id,
            "kind": self.kind,
            "decision": self.decision,
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "vendor": self.vendor,
            "model": self.model,
            "resource": self.resource,
            "window": self.window,
            "current": None if self.current is None else round(self.current, 8),
            "requested": (
                None if self.requested is None else round(self.requested, 8)
            ),
            "limit": None if self.limit is None else round(self.limit, 8),
            "warnAt": None if self.warn_at is None else round(self.warn_at, 8),
            "mode": self.mode,
            "outcome": self.outcome,
        }
