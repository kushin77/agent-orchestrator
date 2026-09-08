"""guardrails.dlp — DLP + prompt-injection defense + egress guard (issue #27).

Phase-4 Security & guardrails lane (issue `kushin77/agent-orchestrator#27`,
"23 DLP + prompt-injection defense + egress guard (HMAC per-call)"). Parent:
EPIC-00 (issue #4). Doctrine:
[`../../../AGENTS.md`](../../../AGENTS.md),
[`../../../docs/EXECUTION-PLAN.md`](../../../docs/EXECUTION-PLAN.md),
[`../../../docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md),
[`../../../docs/GOLDEN-RULES.md`](../../../docs/GOLDEN-RULES.md).
See [`README.md`](README.md) for the contract and detection model.

Public surface (import as ``dlp`` with ``guardrails/`` on ``sys.path``):

* :class:`~dlp.catalog.RuleCatalog` / :class:`~dlp.catalog.ScrubRule` — the
  policy catalog (``scrub-rules.yml``) and its strict, fail-closed loader.
* :class:`~dlp.engine.ScrubEngine` / :class:`~dlp.engine.ScrubResult` — the
  outbound scrub gate (block aborts; redact replaces with placeholders).
* :class:`~dlp.injection.InjectionDetector` — prompt-injection heuristics and
  output filtering (blocked/suspicious/benign model).
* :class:`~dlp.egress.EgressGuard` — default-deny per-tenant egress allowlist.
* :class:`~dlp.hmac_audit.HmacSigner` / :class:`~dlp.hmac_audit.HmacAuditLog` —
  per-call HMAC-SHA256 signing and tamper-evident audit.
* :class:`~dlp.telemetry.SecurityTelemetry` — security-view event sink.
* :class:`~dlp.pipeline.EgressPipeline` — the composed gate (scrub ->
  injection -> egress -> HMAC audit) used before any provider dispatch.
"""

from __future__ import annotations

__version__ = "1.0.0"

from .catalog import CatalogError, RuleCatalog, ScrubMatch, ScrubRule  # noqa: F401
from .egress import AllowedTarget, EgressDecision, EgressGuard  # noqa: F401
from .engine import ScrubEngine, ScrubResult, luhn_valid  # noqa: F401
from .hmac_audit import (  # noqa: F401
    AuditIntegrity,
    HmacAuditLog,
    HmacKeyError,
    HmacSigner,
)
from .injection import (  # noqa: F401
    InjectionDetector,
    InjectionReport,
    Signal,
    SignalHit,
    neutralize_untrusted,
    wrap_untrusted,
)
from .pipeline import CallOutcome, EgressPipeline  # noqa: F401
from .telemetry import SecurityEvent, SecurityTelemetry  # noqa: F401

__all__ = [
    # catalog
    "CatalogError",
    "RuleCatalog",
    "ScrubMatch",
    "ScrubRule",
    # egress
    "AllowedTarget",
    "EgressDecision",
    "EgressGuard",
    # engine
    "ScrubEngine",
    "ScrubResult",
    "luhn_valid",
    # hmac_audit
    "AuditIntegrity",
    "HmacAuditLog",
    "HmacKeyError",
    "HmacSigner",
    # injection
    "InjectionDetector",
    "InjectionReport",
    "Signal",
    "SignalHit",
    "neutralize_untrusted",
    "wrap_untrusted",
    # pipeline
    "CallOutcome",
    "EgressPipeline",
    # telemetry
    "SecurityEvent",
    "SecurityTelemetry",
    "__version__",
]
