"""Startup surface gating — ``surfaces.cockpit``, read fail-closed (#566).

The cockpit is a client of flag-gated surfaces, and it is one itself. This
module consumes the flag reader the flag registry's own server half declares
(``portal.server.fleet.read_surface_default``) instead of re-implementing one:
a missing registry, an unreadable or invalid document, a missing ``surfaces``
section, or a missing entry all read as ``"off"``, and only an explicit
``default: on`` turns the surface on. The surface cannot ship enabled by
accident (GR-5 / AO-GR-6).

A cockpit that starts while its flag is off must not render a frame that reads
as healthy: :func:`render_flag_off` is the named ``FLAG_OFF`` condition, and
the CLI exits non-zero on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import _paths

#: The registry surface key that gates the cockpit itself.
SURFACE = "cockpit"


def read_default(
    repo_root: Optional[Path | str] = None,
    registry_path: Optional[Path | str] = None,
) -> str:
    """The registry's declared default for ``surfaces.cockpit`` ("on"/"off").

    Fail-closed: anything unreadable — including a registry the reader cannot
    parse — reads as ``"off"``.
    """
    _paths.ensure_paths()
    import portal.server.fleet as fleet_surface

    try:
        return fleet_surface.read_surface_default(
            repo_root if repo_root is not None else _paths.ROOT,
            registry_path=registry_path,
            surface=SURFACE,
        )
    except Exception:  # noqa: BLE001 - fail closed is the contract, never "on"
        return "off"


def read_surface(
    surface: str,
    repo_root: Optional[Path | str] = None,
    registry_path: Optional[Path | str] = None,
) -> str:
    """The registry's declared default for one surface key ("on"/"off").

    The cockpit names a function's own flags when it renders the function
    disabled (ADR-0026 D10.3): an unpromoted surface is absent, not broken.
    """
    _paths.ensure_paths()
    import portal.server.fleet as fleet_surface

    try:
        return fleet_surface.read_surface_default(
            repo_root if repo_root is not None else _paths.ROOT,
            registry_path=registry_path,
            surface=surface,
        )
    except Exception:  # noqa: BLE001 - fail closed is the contract, never "on"
        return "off"


def enabled(
    repo_root: Optional[Path | str] = None,
    registry_path: Optional[Path | str] = None,
) -> bool:
    """True only when the registry explicitly promotes ``surfaces.cockpit``."""
    return (
        read_default(repo_root=repo_root, registry_path=registry_path) == "on"
    )


class FlagState:
    """The surface states the cockpit renders with, read once, fail-closed.

    Each function's own ``flags`` decide whether its panel renders at all
    (ADR-0026 D10.3): while one of its surfaces is off, the panel renders the
    ``disabled`` condition naming that flag — "off" is never shown as data.
    """

    def __init__(
        self,
        repo_root: Optional[Path | str] = None,
        registry_path: Optional[Path | str] = None,
    ) -> None:
        self._cache: dict[str, str] = {}
        self._repo_root = repo_root
        self._registry_path = registry_path

    def state(self, surface: str) -> str:
        if surface not in self._cache:
            self._cache[surface] = read_surface(
                surface, self._repo_root, self._registry_path
            )
        return self._cache[surface]


def render_flag_off(flag: str = "surfaces.cockpit") -> str:
    """The named ``FLAG_OFF`` frame — never a silent no-op, never healthy."""
    return "\n".join(
        (
            "cockpit: FLAG_OFF",
            f"  {flag} is off — this surface is absent, not broken",
            "  the cockpit refuses to start while its flag is off (GR-5)",
        )
    )
