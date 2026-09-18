"""Live head-agent registration + reachability projection (issue #889, lane L10).

``project`` is a pure read of two real stores, never a serialised copy:

* the **catalog** — ``gateway/catalog/modules/<id>/module.json`` on disk,
  re-read from the filesystem on every call (mirrors the catalog-parity test's
  own read in ``gateway/catalog/tests/test_catalog_parity.py``);
* the **health monitor** — an injected :class:`health.monitor.HealthMonitor`
  (or ``None`` when no live monitor is wired yet, in which case reachability
  is honestly reported ``"unknown"`` rather than fabricated).

No-false-green: an unregistered/unknown catalog module id is refused BY NAME
(:class:`UnknownCatalogModule`), never silently reported as unreachable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

#: hermes is the registered head agent this projection serves (issue #889).
HEAD_AGENT_ID = "hermes"

REACHABLE = "reachable"
UNREACHABLE = "unreachable"
UNKNOWN = "unknown"


class UnknownCatalogModule(Exception):
    """Raised when the requested catalog module id has no ``module.json``."""

    def __init__(self, module_id: str, modules_dir: Path):
        self.module_id = module_id
        self.modules_dir = modules_dir
        super().__init__(
            f"unknown catalog module: {module_id!r} — no {module_id}/module.json "
            f"under {modules_dir}"
        )


def _catalog_dir(gateway_root: Path) -> Path:
    return gateway_root / "catalog" / "modules"


def read_catalog_entry(gateway_root: Path, module_id: str) -> Dict[str, Any]:
    """Read ``gateway/catalog/modules/<module_id>/module.json`` off disk.

    Raises :class:`UnknownCatalogModule` (named, not a generic KeyError) when
    the module directory or its ``module.json`` is absent — the negative
    control this package's tests exercise.
    """
    modules_dir = _catalog_dir(gateway_root)
    entry_path = modules_dir / module_id / "module.json"
    if not entry_path.is_file():
        raise UnknownCatalogModule(module_id, modules_dir)
    return json.loads(entry_path.read_text(encoding="utf-8"))


def _reachability(monitor: Optional[Any], provider: str, model: str) -> str:
    """Ask the injected live ``HealthMonitor`` — never fabricate a verdict."""
    if monitor is None:
        return UNKNOWN
    try:
        return REACHABLE if monitor.is_healthy(provider, model) else UNREACHABLE
    except Exception:
        # A monitor that has never seen this (provider, model) pair yet is an
        # honest "unknown", not a fabricated reachable/unreachable.
        return UNKNOWN


def project(
    gateway_root: Path | str,
    monitor: Optional[Any] = None,
    module_id: str = HEAD_AGENT_ID,
    model: str = "hermes3",
) -> Dict[str, Any]:
    """The live head-agent registration + reachability document.

    ``gateway_root`` is the ``gateway/`` directory (tests point it at a
    fixture tree so the catalog read stays real but offline). ``monitor`` is
    a live :class:`health.monitor.HealthMonitor`; omitted, reachability is
    reported ``"unknown"`` rather than guessed.
    """
    root = Path(gateway_root)
    entry = read_catalog_entry(root, module_id)
    provider_id = (entry.get("distribution") or {}).get("package", "")
    provider_name = provider_id.rsplit(".", 1)[-1] if provider_id else module_id
    return {
        "schema": "ao.gateway.sync/head-agent-v1",
        "head_agent": module_id,
        "is_head": module_id == HEAD_AGENT_ID,
        "catalog_id": entry.get("id"),
        "catalog_class": entry.get("class", []),
        "provider": provider_name,
        "reachability": _reachability(monitor, provider_name, model),
    }
