"""Controls registry — toggleable, default-OFF switches for policy gates.

Issue #26 acceptance #3 (mirroring ``CMR guardrails/policy/controls.yaml``):
a control is the smallest rollout unit — "a thing a web toggle flips".  Each
control ships ``enabled: false`` by default (AO-GR-6, flag-gated OFF
doctrine); flipping one ON is the deliberate, reviewed act that activates the
policies gated behind it.  A control that ships ON must document
``on_since_rationale`` or the registry fails validation.

The registry is *data*, validated against ``schema/controls.schema.json`` plus
semantic rules (duplicate ids, mode/off consistency, ON-without-rationale).
The :class:`PolicyEngine` consults it to decide whether a control-gated policy
is active; a policy whose control is not registered is rejected by the startup
gate (otherwise it would silently never enforce — an AO-GR-4 formality).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import yaml

from policy.errors import ControlError
from policy.schemas import CONTROLS_SCHEMA_PATH, SchemaValidationError, load_schema, validate

#: Decision modes a control may declare.  ``off`` is a registered-but-wired-nowhere
#: control and must stay disabled.
VALID_MODES = frozenset({"block", "warn", "log", "off"})


@dataclass(frozen=True)
class Control:
    """One toggleable guardrail control."""

    id: str
    name: str
    description: str
    enabled: bool
    mode: str
    implemented_by: tuple[str, ...]
    since: str
    on_since_rationale: str = ""


class ControlRegistry:
    """In-memory controls registry keyed by control id."""

    def __init__(self, controls: Sequence[Control] = ()) -> None:
        ids = [control.id for control in controls]
        duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
        if duplicates:
            raise ControlError(f"duplicate control id(s): {', '.join(duplicates)}")
        self._controls = {control.id: control for control in controls}

    # -- construction ------------------------------------------------------
    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "ControlRegistry":
        """Build and validate a registry from a parsed controls.yaml mapping."""
        try:
            validate(mapping, _schema())
        except SchemaValidationError as exc:
            raise ControlError("invalid controls registry\n" + str(exc)) from exc

        problems: list[str] = []
        seen: set[str] = set()
        for index, control in enumerate(mapping.get("controls") or []):
            cid = control.get("id")
            if cid in seen:
                problems.append(f"controls[{index}]: duplicate control id {cid!r}")
            seen.add(cid)
            mode = control.get("mode")
            if mode not in VALID_MODES:
                problems.append(f"controls[{index}]: invalid mode {mode!r}")
            enabled = control.get("enabled")
            if enabled is True and mode == "off":
                problems.append(
                    f"controls[{index}] {cid!r}: mode 'off' requires enabled: false"
                )
            if enabled is True and not str(control.get("on_since_rationale", "")).strip():
                problems.append(
                    f"controls[{index}] {cid!r}: enabled control must document "
                    "on_since_rationale (AO-GR-6)"
                )
        if problems:
            raise ControlError("invalid controls registry\n" + "\n".join(
                f"- {problem}" for problem in problems
            ))

        return cls(
            [
                Control(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description", ""),
                    enabled=bool(item["enabled"]),
                    mode=item["mode"],
                    implemented_by=tuple(item.get("implemented_by") or ()),
                    since=item.get("since", ""),
                    on_since_rationale=item.get("on_since_rationale", ""),
                )
                for item in mapping.get("controls") or []
            ]
        )

    @classmethod
    def load_yaml(cls, path: str | Path) -> "ControlRegistry":
        """Load and validate a controls registry from a YAML file."""
        path = Path(path)
        try:
            with open(path, encoding="utf-8") as handle:
                mapping = yaml.safe_load(handle)
        except OSError as exc:
            raise ControlError(f"cannot read controls registry {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ControlError(f"cannot parse controls registry {path}: {exc}") from exc
        if not isinstance(mapping, Mapping):
            raise ControlError(f"{path}: controls registry must be a mapping")
        return cls.from_mapping(mapping)

    # -- lookup ------------------------------------------------------------
    def get(self, control_id: str) -> Optional[Control]:
        return self._controls.get(control_id)

    def __contains__(self, control_id: object) -> bool:
        return control_id in self._controls

    def __len__(self) -> int:
        return len(self._controls)

    def ids(self) -> tuple[str, ...]:
        return tuple(self._controls)

    def is_active(self, control_id: str) -> bool:
        """A control is active only when registered AND enabled (default OFF)."""
        control = self._controls.get(control_id)
        return control is not None and control.enabled

    def active_ids(self) -> tuple[str, ...]:
        return tuple(cid for cid, control in self._controls.items() if control.enabled)

    def all(self) -> tuple[Control, ...]:
        return tuple(self._controls.values())


def _schema() -> Any:
    here = Path(__file__).resolve().parent
    return load_schema(str(here / CONTROLS_SCHEMA_PATH))
