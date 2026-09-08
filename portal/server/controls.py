"""portal.server.controls — console policy controls (no UI-only state).

Every toggle in the Policies/Controls view is bound, server-side, to a
:class:`PolicyControl` through the :data:`CONTROL_POLICY_MAP` mapping table.
The catalog consumes the frozen default-OFF vocabulary of the guardrails
policy controls registry (issue #26, ``guardrails/policy/controls.yaml``) —
each control is "a thing a web toggle flips", ships ``enabled: false`` by
default (AO-GR-6 flag-gated-OFF doctrine) and flipping one ON is the
deliberate act that activates the policies gated behind it.

The portal catalog adds tenant-scale operational controls (also default OFF)
whose enforcement lives in :class:`PolicyEnforcer`; each entry's
``implemented_by`` records the merged pillar rail it mirrors
(``telemetry/budgets`` kill-switch rail, ``registry/prompts`` feedback loop).

State lives in :class:`PolicyStateStore` (per-tenant, server-side), never in
the browser. :class:`PolicyEnforcer.evaluate` turns that state into an
enforcement decision, so a toggled control demonstrably changes policy state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(f"portal controls need PyYAML: {exc}") from exc

#: Every console toggle id -> the policy control it flips (server-side map).
#: ``gated_actions`` is the enforcement semantics of the mapped control.
CONTROL_POLICY_MAP: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class PolicyControl:
    """One toggleable console policy control (guardrails Control shape)."""

    id: str
    name: str
    description: str
    mode: str  # block | warn | log | off
    source: str  # guardrails | portal
    gated_actions: tuple[str, ...] = ()
    implemented_by: tuple[str, ...] = ()
    since: str = ""
    enabled_default: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "source": self.source,
            "gatedActions": list(self.gated_actions),
            "implementedBy": list(self.implemented_by),
            "since": self.since,
            "enabledDefault": self.enabled_default,
        }


#: Enforcement semantics for the guardrails platform controls (issue #26). The
#: controls registry declares *what* each control gates; the action it blocks
#: at the console boundary is this lane's documented mapping.
_GUARDRAILS_GATED_ACTIONS: Mapping[str, tuple[str, ...]] = {
    "model-call-budget": ("model.call",),
    "tool-use-guard": ("agent.tool",),
    "data-egress-guard": ("egress.send",),
}

VALID_MODES = frozenset({"block", "warn", "log", "off"})


class ControlCatalog:
    """Registered console policy controls (guardrails + portal catalogs)."""

    def __init__(self, controls: Iterable[PolicyControl]) -> None:
        self._controls: dict[str, PolicyControl] = {}
        for control in controls:
            if control.id in self._controls:
                raise ValueError(f"duplicate control id {control.id!r}")
            if control.mode not in VALID_MODES:
                raise ValueError(f"control {control.id!r}: invalid mode {control.mode!r}")
            self._controls[control.id] = control

    def all(self) -> list[PolicyControl]:
        return list(self._controls.values())

    def get(self, control_id: str) -> Optional[PolicyControl]:
        return self._controls.get(control_id)

    def __contains__(self, control_id: str) -> bool:
        return control_id in self._controls

    def __len__(self) -> int:
        return len(self._controls)

    # -- builders -----------------------------------------------------------
    @classmethod
    def load_guardrails(cls, path: Path) -> list[PolicyControl]:
        """Consume the guardrails controls registry (default-OFF vocabulary).

        Only the id/name/description/mode/default state are consumed — the
        registry is frozen data owned by the guardrails lane (issue #26).
        """
        if not path.exists():
            raise FileNotFoundError(
                f"guardrails controls registry not found at {path}"
            )
        mapping = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        controls: list[PolicyControl] = []
        for item in mapping.get("controls") or []:
            cid = str(item["id"])
            controls.append(
                PolicyControl(
                    id=cid,
                    name=str(item.get("name") or cid),
                    description=str(item.get("description") or ""),
                    mode=str(item.get("mode") or "block"),
                    source="guardrails",
                    gated_actions=_GUARDRAILS_GATED_ACTIONS.get(cid, ()),
                    implemented_by=tuple(str(x) for x in item.get("implemented_by") or ()),
                    since=str(item.get("since") or ""),
                    enabled_default=bool(item.get("enabled")),
                )
            )
        return controls

    @classmethod
    def load_portal(cls, path: Path) -> list[PolicyControl]:
        """Load this lane's portal controls catalog (default OFF)."""
        if not path.exists():
            raise FileNotFoundError(f"portal controls catalog not found at {path}")
        mapping = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        controls: list[PolicyControl] = []
        for item in mapping.get("controls") or []:
            cid = str(item["id"])
            controls.append(
                PolicyControl(
                    id=cid,
                    name=str(item.get("name") or cid),
                    description=str(item.get("description") or ""),
                    mode=str(item.get("mode") or "block"),
                    source="portal",
                    gated_actions=tuple(str(x) for x in item.get("gated_actions") or ()),
                    implemented_by=tuple(str(x) for x in item.get("implemented_by") or ()),
                    since=str(item.get("since") or ""),
                    enabled_default=bool(item.get("enabled")),
                )
            )
        return controls

    @classmethod
    def build(
        cls,
        *,
        repo_root: Path,
        guardrails_yaml: Optional[Path] = None,
        portal_yaml: Optional[Path] = None,
    ) -> "ControlCatalog":
        guardrails_path = guardrails_yaml or (
            repo_root / "guardrails" / "policy" / "controls.yaml"
        )
        portal_path = portal_yaml or (
            repo_root / "portal" / "catalog" / "policy-controls.yaml"
        )
        controls = cls.load_guardrails(guardrails_path)
        controls.extend(cls.load_portal(portal_path))
        return cls(controls)


def build_control_policy_map(catalog: ControlCatalog) -> dict[str, dict[str, Any]]:
    """The server-side mapping table: control id -> policy control record."""
    result: dict[str, dict[str, Any]] = {}
    for control in catalog.all():
        result[control.id] = {
            "policy": control.id,
            "name": control.name,
            "mode": control.mode,
            "source": control.source,
            "gated_actions": list(control.gated_actions),
            "implemented_by": list(control.implemented_by),
        }
    return result


class PolicyStateStore:
    """Server-side per-tenant policy state (default OFF, AO-GR-6).

    The store is the single source of truth for what a control toggle means;
    the browser never holds or reports policy state.
    """

    def __init__(self, catalog: ControlCatalog, tenants: Iterable[str] = ()) -> None:
        self._catalog = catalog
        self._state: dict[str, dict[str, bool]] = {
            tenant: {control.id: control.enabled_default for control in catalog.all()}
            for tenant in tenants
        }

    def ensure_tenant(self, tenant_id: str) -> None:
        if tenant_id not in self._state:
            self._state[tenant_id] = {
                control.id: control.enabled_default for control in self._catalog.all()
            }

    def set_control(self, tenant_id: str, control_id: str, enabled: bool) -> PolicyControl:
        """Flip one control for one tenant. Raises KeyError for unknown ids."""
        control = self._catalog.get(control_id)
        if control is None:
            raise KeyError(f"unknown control {control_id!r}")
        if control.mode == "off" and enabled:
            raise ValueError(f"control {control_id!r} mode 'off' cannot be enabled")
        self.ensure_tenant(tenant_id)
        self._state[tenant_id][control_id] = bool(enabled)
        return control

    def is_enabled(self, tenant_id: str, control_id: str) -> bool:
        self.ensure_tenant(tenant_id)
        return bool(self._state[tenant_id].get(control_id, False))

    def enabled_ids(self, tenant_id: str) -> list[str]:
        self.ensure_tenant(tenant_id)
        return [cid for cid, on in self._state[tenant_id].items() if on]

    def snapshot(self, tenant_id: str) -> dict[str, bool]:
        self.ensure_tenant(tenant_id)
        return dict(self._state[tenant_id])

    def controls_for_tenant(self, tenant_id: str, catalog: ControlCatalog) -> list[dict[str, Any]]:
        state = self.snapshot(tenant_id)
        return [
            {**control.as_json(), "enabled": bool(state[control.id])}
            for control in catalog.all()
        ]


class PolicyEnforcer:
    """Turns stored control state into enforcement decisions.

    ``evaluate`` mirrors the guardrails PolicyEngine decision ladder: an
    action gated by an enabled ``block`` control is refused; a ``warn``
    control warns; an uncovered action is allowed. Defaults are always safe.
    """

    def __init__(self, store: PolicyStateStore, catalog: ControlCatalog) -> None:
        self._store = store
        self._catalog = catalog

    def evaluate(self, tenant_id: str, action: str) -> dict[str, Any]:
        decision: str = "allow"
        hit: Optional[PolicyControl] = None
        for control in self._catalog.all():
            if action not in control.gated_actions:
                continue
            if not self._store.is_enabled(tenant_id, control.id):
                continue
            decision = control.mode if control.mode in ("block", "warn", "log") else "allow"
            hit = control
            break
        return {
            "decision": decision,
            "action": action,
            "tenantId": tenant_id,
            "control": hit.id if hit else None,
        }

    def is_blocked(self, tenant_id: str, action: str) -> bool:
        return self.evaluate(tenant_id, action)["decision"] == "block"
