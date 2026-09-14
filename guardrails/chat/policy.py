"""guardrails.chat.policy — binding a chat turn to the controls registry.

Issue #507 acceptance #5: a chat turn honours the ``guardrails/policy``
counters registry — the default-OFF controls the portal's Policies view toggles
(``guardrails/policy/controls.yaml``, consumed READ-ONLY through
``policy.controls.ControlRegistry``, issue #26). Flipping a control that this
lane binds must genuinely change the turn's observed verdict.

Two controls are bound, each with one documented effect:

======================  =====================================================
control id              effect when ON
======================  =====================================================
``data-egress-guard``   an outbound component whose scrub found redaction-class
                        material is refused (BLOCK) instead of sent redacted
                        (WARN)
``tool-use-guard``      tool-call arguments are additionally analysed for
                        prompt injection before dispatch, and a blocked
                        analysis aborts the turn
======================  =====================================================

Fail-closed binding (never "the control was missing, so carry on"):

* an **unreadable or invalid registry** makes the binding *undecidable* — the
  turn refuses (``ran=False``, BLOCK) instead of assuming every control is off;
* a **bound control that is not registered** is undecidable for the same
  reason — a control-gated behaviour with no toggle in the registry is a
  formality (AO-GR-4), not a gate;
* consulting a control this lane does **not** bind raises, so a control can
  never be read as if it gated something it does not.

Enabling a control goes through the same validation the portal's write path
uses (:meth:`ControlRegistry.from_mapping`), so an enabled control still has to
carry its ``on_since_rationale`` (AO-GR-6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import yaml

from policy.controls import ControlRegistry
from policy.errors import ControlError

#: Repo-relative location of the shipped controls registry (read-only).
CONTROLS_RELPATH = ("guardrails", "policy", "controls.yaml")

#: Rationale stamped on a control this lane enables for one invocation.
ENABLED_RATIONALE = "enabled for this invocation by the chat guard (issue #507)"


class ControlBindingError(RuntimeError):
    """A control the chat guard consults could not be resolved."""


@dataclass(frozen=True)
class ControlBindingSpec:
    """One control the chat lane binds, with the behaviour it gates."""

    control_id: str
    effect: str


CHAT_CONTROL_BINDINGS: tuple = (
    ControlBindingSpec(
        control_id="data-egress-guard",
        effect=(
            "when ON, an outbound component whose DLP scrub found redaction-class "
            "material is refused (BLOCK) instead of sent redacted (WARN)"
        ),
    ),
    ControlBindingSpec(
        control_id="tool-use-guard",
        effect=(
            "when ON, tool-call arguments are analysed for prompt injection before "
            "dispatch and a blocked analysis aborts the turn"
        ),
    ),
)


def bound_ids() -> tuple:
    """Every control id the chat lane binds, in declaration order."""
    return tuple(spec.control_id for spec in CHAT_CONTROL_BINDINGS)


def default_controls_path(root: Optional[Path] = None) -> Path:
    """Absolute path of the shipped controls registry."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    return base.joinpath(*CONTROLS_RELPATH)


def _read_mapping(path: Path) -> Dict[str, Any]:
    """Read a controls registry mapping, raising :class:`ControlBindingError`."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ControlBindingError(f"cannot read controls registry {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ControlBindingError(f"cannot parse controls registry {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ControlBindingError(f"controls registry {path} is not a mapping")
    return dict(document)


class ControlBinding:
    """A resolved (or explicitly unresolvable) view of the controls registry."""

    def __init__(self, registry: Optional[ControlRegistry] = None, *, error: str = "") -> None:
        self._registry = registry
        self._error = error

    # -- construction ------------------------------------------------------

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "ControlBinding":
        try:
            return cls(ControlRegistry.from_mapping(document))
        except ControlError as exc:
            return cls(None, error=f"invalid controls registry: {exc}")

    @classmethod
    def from_path(cls, path: Path) -> "ControlBinding":
        try:
            document = _read_mapping(Path(path))
        except ControlBindingError as exc:
            return cls(None, error=str(exc))
        return cls.from_mapping(document)

    @classmethod
    def default(cls) -> "ControlBinding":
        """The shipped registry — every control default OFF."""
        return cls.from_path(default_controls_path())

    @classmethod
    def with_controls_enabled(
        cls, control_ids: Sequence[str], *, path: Optional[Path] = None
    ) -> "ControlBinding":
        """The registry with ``control_ids`` flipped ON.

        This is the portal toggle expressed as data: the flip is applied to the
        registry document and then re-validated exactly as the console's write
        path validates it. Naming a control the registry does not carry yields
        an *undecidable* binding (never a silent no-op flip).
        """
        wanted = tuple(control_ids)
        if not wanted:
            return cls(None, error="no control id was supplied to enable")
        try:
            document = _read_mapping(Path(path) if path is not None else default_controls_path())
        except ControlBindingError as exc:
            return cls(None, error=str(exc))

        controls = document.get("controls") or []
        present = {control.get("id") for control in controls if isinstance(control, Mapping)}
        unknown = [control_id for control_id in wanted if control_id not in present]
        if unknown:
            return cls(
                None,
                error="control(s) not present in the controls registry: " + ", ".join(sorted(unknown)),
            )
        for control in controls:
            if isinstance(control, Mapping) and control.get("id") in wanted:
                control["enabled"] = True
                control["on_since_rationale"] = ENABLED_RATIONALE
        return cls.from_mapping(document)

    # -- state -------------------------------------------------------------

    @property
    def undecidable(self) -> bool:
        """True when the registry could not be read or validated."""
        return self._registry is None

    @property
    def error(self) -> str:
        """Why the binding is undecidable (empty when it resolved)."""
        return self._error or ("controls registry unavailable" if self._registry is None else "")

    def registered_ids(self) -> tuple:
        if self._registry is None:
            raise ControlBindingError(f"controls registry unavailable: {self.error}")
        return self._registry.ids()

    def bound_ids(self) -> tuple:
        """Every control id this lane binds, in declaration order."""
        return bound_ids()

    def bound_missing(self) -> tuple:
        """Bound control ids that the registry does not carry."""
        if self._registry is None:
            return bound_ids()
        return tuple(control_id for control_id in bound_ids() if control_id not in self._registry)

    def is_active(self, control_id: str) -> bool:
        """Whether ``control_id`` is registered, bound by this lane, and ON."""
        if self._registry is None:
            raise ControlBindingError(f"controls registry unavailable: {self.error}")
        if control_id not in bound_ids():
            raise ControlBindingError(f"control {control_id!r} is not bound by the chat guard")
        control = self._registry.get(control_id)
        if control is None:
            raise ControlBindingError(
                f"control {control_id!r} is not registered in the controls registry"
            )
        return bool(control.enabled)

    def active_ids(self) -> tuple:
        """Every bound control currently ON (empty on an undecidable binding)."""
        if self._registry is None:
            return ()
        return tuple(control_id for control_id in bound_ids() if self._registry.is_active(control_id))

    def describe(self) -> Dict[str, Any]:
        """A record-safe summary of the binding (ids only, never a payload)."""
        if self._registry is None:
            return {"undecidable": True, "error": self.error}
        return {
            "undecidable": False,
            "bound": list(bound_ids()),
            "registered": list(self._registry.ids()),
            "active": list(self.active_ids()),
        }
