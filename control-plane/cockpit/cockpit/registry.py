"""RC-10 consumption — the function registry and the headless renderer (#566).

The cockpit renders ONLY what the registry declares, and it renders it with the
RC-10 renderer — never with a hand-built string of markup. This module is the
one seam that loads both:

* :func:`load` — ``control-plane/functions/cockpit_registry.Registry``;
* :func:`modules` — the ``cockpit_registry`` and ``cockpit_render`` modules,
  imported from the directory they live in exactly as their own tests import
  them, so the cockpit cannot drift into a copy;
* :func:`panel_fixtures` — the committed panel fixtures (the same
  ``load_fixtures`` technique ``cockpit_render`` proves);
* :func:`resolve` — one operator invocation resolved into the endpoint and argv
  the control API would forward, refusing an unknown parameter by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from . import _paths

_CACHE: dict[str, Any] = {}


def _modules() -> tuple[Any, Any]:
    """(cockpit_registry module, cockpit_render module), cached per process."""
    cached = _CACHE.get("modules")
    if cached is not None:
        return cached
    _paths.ensure_paths()
    import cockpit_registry
    import cockpit_render

    _CACHE["modules"] = (cockpit_registry, cockpit_render)
    return (cockpit_registry, cockpit_render)


def load(path: Optional[Path | str] = None) -> Any:
    """The declared function registry (RC-10's own loader)."""
    key = f"registry:{path}"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    reg_module, _ = _modules()
    registry = reg_module.load(Path(path) if path is not None else None)
    _CACHE[key] = registry
    return registry


def modules() -> tuple[Any, Any]:
    """The two RC-10 modules the cockpit renders with."""
    return _modules()


def panel_fixtures() -> dict[str, Any]:
    """The committed panel fixtures, by function id."""
    cached = _CACHE.get("fixtures")
    if cached is not None:
        return cached
    _, render_module = _modules()
    fixtures = render_module.load_fixtures()
    _CACHE["fixtures"] = fixtures
    return fixtures


def resolve(function_id: str, given: Mapping[str, Any]) -> tuple[Any, list[Any]]:
    """Resolve one operator invocation against the declared registry."""
    reg_module, _ = _modules()
    return reg_module.resolve_call(load(), function_id, given)


def workspace_ids(registry: Any, role: str) -> list[str]:
    """A role's recommended function ids — a lens, never a permission set."""
    reg_module, _ = _modules()
    return list(reg_module.render_workspace(registry, role))
