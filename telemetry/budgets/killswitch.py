"""telemetry/budgets — global kill switch (platform-wide pause) (issue #34).

A single switch that halts all **non-critical** billable model calls
platform-wide: while it is engaged every prospective call that is not
explicitly critical (or on an exempt service) is refused *before* dispatch —
spend stops instantly, regardless of that tenant's budget/quota headroom
(cannibalized from shared-services ``global_pause.py``: the check-before-act
flag, the owner-triggerable set/clear with reason, and the hard-stop that
blocks even approved actions).

Safe-rollout doctrine (new controls default OFF): the shipped state is
``globalPause: false`` (``config/killswitch.yaml``).  An operator engages it
with :meth:`KillSwitchController.pause` (reason + who + timestamp recorded)
and it stays engaged until :meth:`KillSwitchController.clear`.  The runtime
flag may also come from the ``GLOBAL_PAUSE`` env var (``true``/``1``/``yes``)
at boot, mirroring the cannibalized source.

Every engagement/clear and every refusal is recorded to the injected audit
store (acceptance: global pause with audit).  A refusal maps onto the
metering non-billable outcome ``refused`` (issue #33 ``NON_BILLABLE_OUTCOMES``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, Tuple

import yaml

from telemetry.budgets.audit import (
    EVENT_CLEAR,
    EVENT_EXEMPT,
    EVENT_PAUSE,
    BudgetAuditEvent,
    BudgetAuditStore,
    MemoryAuditStore,
    record_decision,
)
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_REFUSE,
    EnforcerDecision,
    KIND_KILL_SWITCH,
    OUTCOME_REFUSED,
    now_utc_iso,
)

DEFAULT_KILLSWITCH_CONFIG = (
    Path(__file__).resolve().parent / "config" / "killswitch.yaml"
)

#: Environment override consumed at boot (cannibalized from global_pause.py).
_ENV_PAUSE = "GLOBAL_PAUSE"


@dataclass(frozen=True)
class KillSwitchState:
    """The kill-switch flag plus why/who/when it was engaged."""

    global_pause: bool = False
    reason: Optional[str] = None
    paused_by: Optional[str] = None
    timestamp: Optional[str] = None
    exempt_services: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "globalPause": self.global_pause,
            "reason": self.reason,
            "pausedBy": self.paused_by,
            "timestamp": self.timestamp,
            "exemptServices": list(self.exempt_services),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "KillSwitchState":
        if not isinstance(payload, dict):
            raise ValueError("kill-switch state payload must be a mapping")
        exempt = payload.get("exemptServices") or []
        return cls(
            global_pause=bool(payload.get("globalPause", False)),
            reason=payload.get("reason"),
            paused_by=payload.get("pausedBy"),
            timestamp=payload.get("timestamp"),
            exempt_services=tuple(str(s) for s in exempt),
        )


class KillSwitchStore(Protocol):
    """Reads/writes the durable kill-switch state."""

    def load(self) -> KillSwitchState:
        """The current durable state."""
        raise NotImplementedError

    def save(self, state: KillSwitchState) -> None:
        """Persist a state transition."""
        raise NotImplementedError


class MemoryKillSwitchStore:
    """In-memory state store (tests/offline)."""

    def __init__(self, initial: Optional[KillSwitchState] = None) -> None:
        self._state = initial or KillSwitchState()

    def load(self) -> KillSwitchState:
        return self._state

    def save(self, state: KillSwitchState) -> None:
        self._state = state


class JsonKillSwitchStore:
    """Durable JSON state store (the file is the source of truth)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> KillSwitchState:
        if not self.path.is_file():
            return KillSwitchState()
        try:
            payload = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"cannot read kill-switch state {self.path}: {exc}")
        return KillSwitchState.from_dict(payload)

    def save(self, state: KillSwitchState) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(state.to_dict(), fh, sort_keys=False)


def load_killswitch_state(
    path: Path = DEFAULT_KILLSWITCH_CONFIG,
    *,
    env: Optional[dict] = None,
) -> KillSwitchState:
    """Load the shipped OFF-by-default config (optionally overridden by env).

    The ``GLOBAL_PAUSE`` env var (true/1/yes) engages the switch at boot —
    the cannibalized source's debug/incident override, useful before the
    state store is reachable.
    """
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    state = KillSwitchState.from_dict(payload)
    env = dict(os.environ if env is None else env)
    flag = env.get(_ENV_PAUSE)
    if flag is not None and str(flag).lower() in ("true", "1", "yes"):
        return KillSwitchState(
            global_pause=True,
            reason=state.reason or "GLOBAL_PAUSE environment override",
            paused_by=state.paused_by or "env",
            timestamp=state.timestamp or now_utc_iso(),
            exempt_services=state.exempt_services,
        )
    return state


class KillSwitchController:
    """The platform-wide pause controller.

    ``store`` supplies the durable state (defaults to in-memory so the class
    is usable offline); ``audit`` records pause/clear/refuse/exempt events.
    A deployment injects a file-backed store + the shared audit store.
    """

    def __init__(
        self,
        store: Optional[KillSwitchStore] = None,
        *,
        audit: Optional[BudgetAuditStore] = None,
        initial: Optional[KillSwitchState] = None,
    ) -> None:
        self.store = store or MemoryKillSwitchStore()
        self.audit = audit or MemoryAuditStore()
        if initial is not None:
            self.store.save(initial)

    @property
    def state(self) -> KillSwitchState:
        return self.store.load()

    @property
    def paused(self) -> bool:
        return self.store.load().global_pause

    # ------------------------------------------------------------------ #
    def pause(self, reason: str, paused_by: Optional[str] = None) -> KillSwitchState:
        """Engage the global kill switch (owner-triggerable, audited)."""
        if not reason:
            raise ValueError("a reason is required to engage the kill switch")
        state = KillSwitchState(
            global_pause=True,
            reason=reason,
            paused_by=paused_by or "operator",
            timestamp=now_utc_iso(),
            exempt_services=self.state.exempt_services,
        )
        self.store.save(state)
        self.audit.record(
            BudgetAuditEvent(
                kind=KIND_KILL_SWITCH,
                event_type=EVENT_PAUSE,
                tenant_id="*",
                decision="refuse",
                code="kill_switch.pause",
                reason=reason,
                agent_id=paused_by,
            )
        )
        return state

    def clear(self) -> KillSwitchState:
        """Clear the kill switch (audited)."""
        state = KillSwitchState(
            global_pause=False,
            reason=None,
            paused_by=None,
            timestamp=now_utc_iso(),
            exempt_services=self.state.exempt_services,
        )
        self.store.save(state)
        self.audit.record(
            BudgetAuditEvent(
                kind=KIND_KILL_SWITCH,
                event_type=EVENT_CLEAR,
                tenant_id="*",
                decision="allow",
                code="kill_switch.clear",
                reason="kill switch cleared",
            )
        )
        return state

    # ------------------------------------------------------------------ #
    def check_call(
        self,
        tenant_id: str,
        *,
        agent_id: Optional[str] = None,
        service: Optional[str] = None,
        critical: bool = False,
    ) -> EnforcerDecision:
        """Pre-flight one call against the global kill switch.

        While paused, any call that is not critical (and not on an exempt
        service) is REFUSED — a normally-allowed call is refused the instant
        the switch is ON.  Critical spend (the ``critical`` flag or an exempt
        service) is allowed through a pause but recorded as an ``exempt``
        audit event — never silently allowed.
        """
        if not self.paused:
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_KILL_SWITCH,
                decision=DECISION_ALLOW,
                reason="kill switch is not engaged",
                code="kill_switch.off",
                agent_id=agent_id,
            )
        if critical or (service is not None and service in self.state.exempt_services):
            self.audit.record(
                BudgetAuditEvent(
                    kind=KIND_KILL_SWITCH,
                    event_type=EVENT_EXEMPT,
                    tenant_id=tenant_id,
                    decision=DECISION_ALLOW,
                    code="kill_switch.exempt",
                    reason=(
                        f"critical/exempt call allowed through global pause "
                        f"(service={service or '-'})"
                    ),
                    agent_id=agent_id,
                )
            )
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_KILL_SWITCH,
                decision=DECISION_ALLOW,
                reason=f"critical/exempt call allowed through global pause (service={service or '-'})",
                code="kill_switch.exempt",
                agent_id=agent_id,
            )
        return self._refuse(tenant_id=tenant_id, agent_id=agent_id)

    def _refuse(
        self, *, tenant_id: str, agent_id: Optional[str]
    ) -> EnforcerDecision:
        decision = EnforcerDecision(
            tenant_id=tenant_id,
            kind=KIND_KILL_SWITCH,
            decision=DECISION_REFUSE,
            reason=(
                "global kill switch engaged: all non-critical model calls are "
                f"refused (reason: {self.state.reason or '-'})"
            ),
            code="kill_switch.global_pause",
            agent_id=agent_id,
            outcome=OUTCOME_REFUSED,
        )
        # Every kill-switch refusal is audited (acceptance: audit).
        record_decision(self.audit, decision)
        return decision
