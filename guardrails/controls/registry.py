"""Load the guardrail control set from ``guardrails/policy/controls.yaml``.

This module is deliberately thin: it does NOT re-implement the controls
registry.  It *consumes* two in-repo surfaces and adapts them to the
server-side model (issue #343):

* ``guardrails/policy/controls.py`` (:class:`policy.controls.ControlRegistry`)
  parses and schema-validates ``controls.yaml`` — the authoritative default-OFF
  vocabulary.  The loader never imports YAML itself, so this lane carries no
  third-party import.
* ``portal/server/controls.py`` (:func:`build_control_policy_map` over
  :class:`ControlCatalog`) provides the server-side ``CONTROL_POLICY_MAP``
  semantics the console toggles are bound to.  We reuse that map rather than
  copying it, and cross-check that every guardrails control is present in it —
  a control the map does not know is a control the console could not toggle.

State persistence lives here too: :func:`load_state` / :func:`save_state` are
the file seam that lets a *second reader* observe a flip made elsewhere.


---knowledge---
module_id: guardrails.controls.registry
system: guardrails
app: controls
solution_class: pattern
patterns: [consume-never-restate, delegate-never-re-derive, schema-validated-loader]
derives_from: portal/server/controls.py
owner_sme: platform-sme
tier: L1
interfaces: [repo_root, default_controls_path, load_control_policy_map, load_controls, cross_check_policy_map, load_state, save_state, build_control_set]
invariants: "every guardrails control must be present in the server-side policy map or the cross-check fails"
gotchas: "the loader never imports YAML itself, so this lane carries no third-party import"
related: ["#343", "#26"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Optional

# ``guardrails/`` has no ``__init__.py`` (mirroring ``engine/``); the sibling
# ``policy`` package is imported with ``guardrails/`` on sys.path.
_here = Path(__file__).resolve().parent
_guardrails_root = _here.parent
_repo_root = _guardrails_root.parent
if str(_guardrails_root) not in sys.path:
    sys.path.insert(0, str(_guardrails_root))

from policy.controls import ControlRegistry  # noqa: E402
from policy.errors import ControlError as PolicyControlError  # noqa: E402

from controls.model import ControlError, ControlSet, PolicyControl  # noqa: E402

DEFAULT_CONTROLS_RELPATH = Path("guardrails") / "policy" / "controls.yaml"
PORTAL_CONTROLS_RELPATH = Path("portal") / "server" / "controls.py"


def repo_root() -> Path:
    """The repository root this lane's module is checked out under."""
    return _repo_root


def default_controls_path(root: Optional[Path] = None) -> Path:
    """Absolute path of the shipped controls registry."""
    return (Path(root) if root else _repo_root) / DEFAULT_CONTROLS_RELPATH


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib guard
        raise ControlError(f"cannot import module {name!r} from {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolves string annotations (PEP 563)
    # via ``sys.modules[cls.__module__]``, which must exist during class body.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise ControlError(f"cannot execute module {name!r} from {path}: {exc}") from exc
    return module


def load_control_policy_map(root: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Reuse ``portal/server/controls.py``'s CONTROL_POLICY_MAP semantics.

    The portal module's ``build_control_policy_map`` turns the console catalog
    (guardrails controls + portal operations) into the server-side mapping the
    console toggles are bound to.  We read it, never redefine it.
    """
    base = Path(root) if root else _repo_root
    module_path = base / PORTAL_CONTROLS_RELPATH
    if not module_path.is_file():
        raise ControlError(f"portal controls module not found at {module_path}")
    module = _load_module(module_path, "ao_portal_server_controls")
    catalog = module.ControlCatalog.build(repo_root=base)
    return module.build_control_policy_map(catalog)


def load_controls(
    path: Optional[Path] = None,
    *,
    root: Optional[Path] = None,
) -> tuple[PolicyControl, ...]:
    """Load and adapt the guardrails controls registry to the model.

    Every adapted :class:`PolicyControl` ships ``default_enabled=False``; a
    registry entry that ships ON is refused by the model even when the policy
    loader would accept it with an ``on_since_rationale``.
    """
    registry_path = Path(path) if path else default_controls_path(root)
    try:
        registry = ControlRegistry.load_yaml(registry_path)
    except PolicyControlError as exc:
        raise ControlError(f"cannot load controls registry {registry_path}: {exc}") from exc

    controls: list[PolicyControl] = []
    for control in registry.all():
        owner = control.implemented_by[0] if control.implemented_by else "guardrails/policy"
        controls.append(
            PolicyControl(
                id=control.id,
                name=control.name,
                description=control.description,
                mode=control.mode,
                owner=owner,
                audit_ref=f"{DEFAULT_CONTROLS_RELPATH.as_posix()}#{control.id}",
                gated_actions=(),
                default_enabled=bool(control.enabled),
            )
        )
    return tuple(controls)


def cross_check_policy_map(
    controls: tuple[PolicyControl, ...],
    policy_map: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    """Ids missing from the reused CONTROL_POLICY_MAP (empty when coherent)."""
    return tuple(control.id for control in controls if control.id not in policy_map)


def load_state(path: str | Path) -> dict[str, bool]:
    """Read a persisted control-state mapping (empty when absent)."""
    state_path = Path(path)
    if not state_path.exists():
        return {}
    data = json.loads(state_path.read_text(encoding="utf-8"))
    controls = data.get("controls") if isinstance(data, Mapping) else None
    if not isinstance(controls, Mapping):
        raise ControlError(f"state file {state_path} has no 'controls' mapping")
    return {str(key): bool(value) for key, value in controls.items()}


def save_state(path: str | Path, state: Mapping[str, bool]) -> None:
    """Atomically persist a control-state mapping for a second reader."""
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "controls": {str(k): bool(v) for k, v in state.items()}}
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(state_path)


def build_control_set(
    path: Optional[Path] = None,
    *,
    root: Optional[Path] = None,
    state: Optional[Mapping[str, bool]] = None,
    state_path: Optional[str | Path] = None,
) -> ControlSet:
    """Build the live :class:`ControlSet` (default OFF, optionally hydrated)."""
    controls = load_controls(path, root=root)
    hydrated = dict(state) if state is not None else load_state(state_path) if state_path else None
    return ControlSet(controls, state=hydrated)
