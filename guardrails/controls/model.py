"""Server-side guardrail control model (issue #343).

A guardrail control is the smallest rollout unit on the control plane — "a
thing a web toggle flips".  This module is the *server-side* half of the shell
Controls view: the shell owns no policy state, it calls this model, and this
model decides and records.

Two invariants are structural, not advisory:

* **Default OFF.**  A :class:`PolicyControl` refuses to be constructed with
  ``default_enabled=True`` (AO-GR-6, flag-gated-OFF doctrine).  Shipping a
  control ON is not a policy choice the model will accept, so the gate's
  self-mutating negative control cannot be satisfied by an ON fixture.
* **Closed guardrail vocabulary.**  Enforcement reports the Portkey-style
  status codes ``PASSED = 246`` / ``BLOCKED = 446``.  The vocabulary is
  ``frozenset``-closed: :func:`status_name` refuses any other code, so a
  control cannot publish a status the consumers do not understand.

The current state lives in :class:`ControlSet` (one boolean per control).  Its
:meth:`ControlSet.toggle` writes exactly one append-only audit record and
refuses an unknown control id — an unknown control is never silently created.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

# -- guardrail pass/fail status codes (Portkey-style, closed vocabulary) -----
# Portkey's guardrail endpoint answers a request with 246 when the guardrail
# passed and 446 when it blocked.  We adopt the same closed pair so a consumer
# can branch on the code alone; the set is closed and never extended silently.
STATUS_PASSED = 246
STATUS_BLOCKED = 446

STATUS_NAMES: Mapping[int, str] = {
    STATUS_PASSED: "PASSED",
    STATUS_BLOCKED: "BLOCKED",
}
GUARDRAIL_STATUS = frozenset(STATUS_NAMES)
PASSED_STATUSES = frozenset({STATUS_PASSED})
BLOCKED_STATUSES = frozenset({STATUS_BLOCKED})

VALID_MODES = frozenset({"block", "warn", "log", "off"})

_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class ControlError(Exception):
    """A guardrail control invariant was violated."""


class UnknownControlError(ControlError):
    """A toggle/read named a control that is not registered."""


def is_status(code: object) -> bool:
    """True when ``code`` belongs to the closed 246/446 vocabulary."""
    return isinstance(code, int) and not isinstance(code, bool) and code in GUARDRAIL_STATUS


def status_name(code: object) -> str:
    """Name for a guardrail status code; refuses anything outside the pair."""
    if is_status(code):
        return STATUS_NAMES[int(code)]
    raise ControlError(
        f"unknown guardrail status code {code!r} — closed vocabulary is "
        f"{STATUS_PASSED} PASSED / {STATUS_BLOCKED} BLOCKED"
    )


@dataclass(frozen=True)
class PolicyControl:
    """One toggleable guardrail control (server-side record).

    ``default_enabled`` is the shipped default and is structurally OFF; the
    *current* state is held by the owning :class:`ControlSet`.  ``owner`` names
    the rail that enforces the control and ``audit_ref`` is the registry
    reference every toggle record cites.
    """

    id: str
    name: str
    description: str
    mode: str
    owner: str
    audit_ref: str
    gated_actions: tuple[str, ...] = ()
    default_enabled: bool = False

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ControlError(f"control id {self.id!r} must match ^[a-z][a-z0-9-]*$")
        if self.mode not in VALID_MODES:
            raise ControlError(f"control {self.id!r}: invalid mode {self.mode!r}")
        if self.default_enabled:
            raise ControlError(
                f"control {self.id!r} must default OFF (AO-GR-6): refusing a "
                f"control that ships ON (default_enabled=True)"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "owner": self.owner,
            "audit_ref": self.audit_ref,
            "gated_actions": list(self.gated_actions),
            "default_enabled": bool(self.default_enabled),
        }


@dataclass(frozen=True)
class ControlState:
    """A control plus its current enabled state (the observable projection)."""

    control: PolicyControl
    enabled: bool = False

    @property
    def status(self) -> int:
        """246 PASSED while OFF; 446 BLOCKED once enabled."""
        return STATUS_BLOCKED if self.enabled else STATUS_PASSED

    @property
    def status_name(self) -> str:
        return status_name(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.control.id,
            "enabled": bool(self.enabled),
            "status": self.status,
            "status_name": self.status_name,
            "mode": self.control.mode,
            "owner": self.control.owner,
            "audit_ref": self.control.audit_ref,
        }


class ControlSet:
    """A set of controls with current state and single-record toggling.

    Constructed default-OFF from the registry (every ``default_enabled`` is
    structurally False) and optionally hydrated from a persisted state mapping
    — which is how a *second reader* observes a flip another process made.
    """

    def __init__(
        self,
        controls: Iterable[PolicyControl],
        state: Optional[Mapping[str, bool]] = None,
    ) -> None:
        self._controls: dict[str, PolicyControl] = {}
        for control in controls:
            if control.id in self._controls:
                raise ControlError(f"duplicate control id {control.id!r}")
            self._controls[control.id] = control
        if not self._controls:
            raise ControlError("control set is empty — a registry with no controls is a formality")
        enabled: dict[str, bool] = {}
        for cid, control in self._controls.items():
            requested = bool(state.get(cid, control.default_enabled)) if state else control.default_enabled
            if requested and control.mode == "off":
                raise ControlError(f"control {cid!r} mode 'off' cannot be enabled")
            enabled[cid] = requested
        self._enabled = enabled

    # -- reads -------------------------------------------------------------
    def ids(self) -> tuple[str, ...]:
        return tuple(self._controls)

    def __contains__(self, control_id: object) -> bool:
        return control_id in self._controls

    def __len__(self) -> int:
        return len(self._controls)

    def control(self, control_id: str) -> PolicyControl:
        try:
            return self._controls[control_id]
        except KeyError:
            raise UnknownControlError(self._unknown(control_id)) from None

    def is_enabled(self, control_id: str) -> bool:
        self.control(control_id)  # refuse unknown ids on read too
        return bool(self._enabled[control_id])

    def status_of(self, control_id: str) -> int:
        return STATUS_BLOCKED if self.is_enabled(control_id) else STATUS_PASSED

    def states(self) -> tuple[ControlState, ...]:
        return tuple(
            ControlState(control=control, enabled=self._enabled[cid])
            for cid, control in self._controls.items()
        )

    def enabled_ids(self) -> tuple[str, ...]:
        return tuple(cid for cid in self._controls if self._enabled[cid])

    def all_default_off(self) -> bool:
        """True when no control is enabled (the shipped posture)."""
        return not any(self._enabled.values())

    def state_dict(self) -> dict[str, Any]:
        return {"version": 1, "controls": dict(self._enabled)}

    # -- writes ------------------------------------------------------------
    def toggle(
        self,
        control_id: str,
        enabled: Optional[bool] = None,
        *,
        actor: str,
        audit_log: Any,
        reason: str = "",
    ) -> Any:
        """Flip one control, writing EXACTLY ONE append-only audit record.

        ``enabled=None`` flips the current state; ``True``/``False`` sets it.
        An unknown control id is refused (never silently created).  Returns the
        audit record that was appended.
        """
        control = self.control(control_id)
        target = (not self._enabled[control_id]) if enabled is None else bool(enabled)
        if target and control.mode == "off":
            raise ControlError(f"control {control_id!r} mode 'off' cannot be enabled")
        before = self._enabled[control_id]
        self._enabled[control_id] = target

        from controls.audit import build_toggle_record

        record = build_toggle_record(
            sequence=audit_log.next_sequence(),
            control_id=control.id,
            actor=actor,
            before=before,
            after=target,
            status_before=STATUS_BLOCKED if before else STATUS_PASSED,
            status_after=STATUS_BLOCKED if target else STATUS_PASSED,
            audit_ref=control.audit_ref,
            reason=reason,
        )
        audit_log.append(record)
        return record

    # -- helpers -----------------------------------------------------------
    def _unknown(self, control_id: str) -> str:
        known = ", ".join(sorted(self._controls)) or "(none)"
        return f"unknown control {control_id!r} — known controls: {known}"


def assert_refuses_default_on(control_id: str = "probe") -> None:
    """Negative control: constructing an ON-by-default control MUST be refused.

    Raises :class:`ControlError` when the refusal works; raises
    :class:`AssertionError` if the model ever accepts a control that ships ON
    (i.e. the invariant stopped biting).
    """
    try:
        PolicyControl(
            id=control_id,
            name="negative control",
            description="must never be accepted",
            mode="block",
            owner="guardrails/controls/tests",
            audit_ref="guardrails/controls#negative-control",
            default_enabled=True,
        )
    except ControlError:
        return
    raise AssertionError(
        f"model accepted a control that defaults ON ({control_id!r}) — default-OFF is not enforced"
    )
