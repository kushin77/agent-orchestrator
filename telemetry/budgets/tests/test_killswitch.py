"""telemetry/budgets — kill-switch tests (issue #34).

Covers the core negative: **kill switch ON -> a normally-allowed call is
refused**, plus critical/exempt passthrough, audit of pause/clear/refuse and
the env override.
"""

from __future__ import annotations

from pathlib import Path

from telemetry.budgets.audit import MemoryAuditStore
from telemetry.budgets.killswitch import (
    DEFAULT_KILLSWITCH_CONFIG,
    KillSwitchController,
    KillSwitchState,
    MemoryKillSwitchStore,
    load_killswitch_state,
)
from telemetry.budgets.model import DECISION_ALLOW, DECISION_REFUSE, OUTCOME_REFUSED


def _controller(**kwargs) -> KillSwitchController:
    audit = MemoryAuditStore()
    return KillSwitchController(audit=audit, **kwargs)


def test_default_state_is_not_paused():
    ks = _controller()
    assert not ks.paused
    d = ks.check_call("acme", service="model")
    assert d.decision == DECISION_ALLOW
    assert d.code == "kill_switch.off"


def test_pause_then_normally_allowed_call_is_refused():
    ks = _controller()
    assert ks.check_call("acme", service="model").decision == DECISION_ALLOW
    ks.pause(reason="incident rehearsal", paused_by="oncall")
    d = ks.check_call("acme", service="model")
    assert d.decision == DECISION_REFUSE
    assert not d.allowed
    assert d.code == "kill_switch.global_pause"
    assert d.outcome == OUTCOME_REFUSED  # metering non-billable outcome


def test_pause_is_hard_stop_across_tenants_and_agents():
    ks = _controller()
    ks.pause(reason="org-wide stop", paused_by="owner")
    assert ks.check_call("globex", agent_id="arch-1").decision == DECISION_REFUSE
    assert ks.check_call("nimbus", agent_id="agent-x").decision == DECISION_REFUSE


def test_critical_call_allowed_through_pause_but_audited():
    audit = MemoryAuditStore()
    ks = KillSwitchController(audit=audit)
    ks.pause(reason="stop the spend", paused_by="owner")
    d = ks.check_call("acme", service="model", critical=True)
    assert d.decision == DECISION_ALLOW
    assert d.code == "kill_switch.exempt"
    kinds = {e.event_type for e in audit.read()}
    assert "exempt" in kinds  # never silently allowed


def test_exempt_service_allowed_through_pause():
    ks = _controller(
        initial=KillSwitchState(global_pause=True, exempt_services=("critical-svc",))
    )
    d = ks.check_call("acme", service="critical-svc")
    assert d.decision == DECISION_ALLOW


def test_resume_restores_normal_operation():
    ks = _controller()
    ks.pause(reason="rehearsal", paused_by="oncall")
    assert ks.paused
    ks.clear()
    assert not ks.paused
    d = ks.check_call("acme", service="model")
    assert d.decision == DECISION_ALLOW


def test_pause_requires_reason():
    ks = _controller()
    try:
        ks.pause(reason="")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_pause_clear_refuse_all_audited():
    audit = MemoryAuditStore()
    ks = KillSwitchController(audit=audit)
    ks.pause(reason="audit me", paused_by="owner")
    ks.check_call("acme", service="model")     # refuse -> audited
    ks.clear()
    events = audit.read()
    event_types = {e.event_type for e in events}
    assert event_types == {"pause", "decision", "clear"}
    codes = {e.code for e in events}
    assert "kill_switch.global_pause" in codes
    assert "kill_switch.pause" in codes
    assert "kill_switch.clear" in codes


def test_memory_store_durability():
    store = MemoryKillSwitchStore()
    ks = KillSwitchController(store=store)
    ks.pause(reason="persist me", paused_by="owner")
    # a second controller over the same store sees the pause (shared state)
    ks2 = KillSwitchController(store=store)
    assert ks2.paused


def test_env_override_engages_at_boot():
    state = load_killswitch_state(
        Path(DEFAULT_KILLSWITCH_CONFIG),
        env={"GLOBAL_PAUSE": "true"},
    )
    assert state.global_pause is True
