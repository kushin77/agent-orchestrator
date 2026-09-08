"""Metering + audit hooks for model calls (issue #15, criterion 4).

Every call through ``ProviderClient`` is stamped with
provider/model/tenant/agent/logical key (contract.CallContext) and emitted as
a ``ModelCallEvent`` to the ``EventRouter``. The gateway proxy (issue #16)
and the phase-5 observability/metering lane consume these events; the router
is the callback seam they plug into:

    registry.add_metering_hook(handler)   # token/latency/cost metering
    registry.add_audit_hook(handler)      # full-trace audit

A hook raising propagates (fail closed): audit/metering records are never
silently lost. Hook handlers must treat event content as untrusted and never
log secrets (events carry no key material by construction - keys live only in
the vault and the in-memory transport request, never in events/logs).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from providers.contract import DEFAULT_TIER
from providers.contract import Usage as CallUsage

# Event statuses (the terminal outcome of one provider call).
STATUS_SUCCESS = "success"
STATUS_RETRY_EXHAUSTED = "retry_exhausted"
STATUS_CIRCUIT_OPEN = "circuit_open"
STATUS_OUTPUT_INVALID = "output_invalid"
STATUS_FAILED = "failed"
STATUS_CONFIG_ERROR = "configuration_error"


@dataclass(frozen=True)
class ModelCallEvent:
    """One stamped model call outcome, for metering and audit hooks."""

    provider: str
    model: str
    tenant_id: str
    agent_id: str
    logical_key: str = DEFAULT_TIER
    status: str = STATUS_SUCCESS
    usage: CallUsage | None = None
    latency_ms: float = 0.0
    attempts: int = 0
    error_type: str | None = None
    error_detail: str | None = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "logical_key": self.logical_key,
            "status": self.status,
            "usage": self.usage.to_dict() if self.usage else None,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
            "ts": self.ts,
        }


CallHook = Callable[[ModelCallEvent], None]


class EventRouter:
    """Fan-out of ``ModelCallEvent`` to metering and audit hook sets.

    Hook exceptions propagate to the caller (fail closed): a lost audit or
    metering record must never be silent.
    """

    def __init__(
        self,
        metering_hooks: list[CallHook] | None = None,
        audit_hooks: list[CallHook] | None = None,
    ) -> None:
        self._metering: list[CallHook] = list(metering_hooks or [])
        self._audit: list[CallHook] = list(audit_hooks or [])

    def add_metering_hook(self, hook: CallHook) -> None:
        self._metering.append(hook)

    def add_audit_hook(self, hook: CallHook) -> None:
        self._audit.append(hook)

    def metering_hooks(self) -> tuple[CallHook, ...]:
        return tuple(self._metering)

    def audit_hooks(self) -> tuple[CallHook, ...]:
        return tuple(self._audit)

    def emit(self, event: ModelCallEvent) -> None:
        for hook in self._metering:
            hook(event)
        for hook in self._audit:
            hook(event)
