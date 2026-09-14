"""gateway.chat.flags — the `surfaces.chat` gate, checked before AuthN (#503).

The registry (``infra/feature-flags/registry.yaml``) is the declaration; this
module is the reader.  Two properties matter and both are structural:

* **Fail closed.** A registry that is missing, unreadable, not a mapping, or
  that carries no ``surfaces.chat`` entry leaves the surface **off**.  There is
  no argument and no environment variable that turns it on: promotion is a
  reviewed edit to the declared registry, never a runtime override.
* **Checked before AuthN.** The flag gate is the first statement of every
  handler, so an unpromoted surface answers ``404 feature_disabled`` to an
  anonymous probe *and* to a valid credential — the surface is invisible, not
  merely unauthorised.

The reader is deliberately small and dependency-light (PyYAML only) because it
runs on the hot path of every request, and it never imports the portal's own
reader: the portal is a **client** of this surface, not its owner.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

try:  # pragma: no cover - the repo declares PyYAML
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(f"gateway.chat: missing dependency ({exc}); need PyYAML") from exc

from .errors import SurfaceDisabled

#: gateway/chat/flags.py -> gateway/chat -> gateway -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The registry document this surface is declared in.
DEFAULT_REGISTRY_PATH = REPO_ROOT / "infra" / "feature-flags" / "registry.yaml"

#: The surface key inside the registry's ``surfaces:`` block.
SURFACE_KEY = "chat"

#: The service key that owns the surface in the registry's ``services:`` block.
SERVICE_KEY = "chat"

OFF = "off"
ON = "on"


def read_surface_default(
    registry_path: Optional[Path] = None,
    surface: str = SURFACE_KEY,
) -> str:
    """The declared default for one surface: ``"off"`` / ``"on"`` / ``""``.

    An absent document, entry or value returns ``""`` — the caller treats that
    as off (fail closed), because "we could not read the declaration" must never
    mean "it is on".
    """
    path = Path(registry_path) if registry_path is not None else DEFAULT_REGISTRY_PATH
    if not path.is_file():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(document, dict):
        return ""
    surfaces = document.get("surfaces")
    if not isinstance(surfaces, dict):
        return ""
    entry = surfaces.get(surface)
    if not isinstance(entry, dict):
        return ""
    # PyYAML parses the bare YAML 1.1 scalar `off` as boolean False; both the
    # boolean and the string form mean OFF (the feature-flags gate accepts both).
    value = entry.get("default")
    if value is False or value == OFF:
        return OFF
    if value is True or value == ON:
        return ON
    return ""


def surface_enabled(
    registry_path: Optional[Path] = None,
    surface: str = SURFACE_KEY,
) -> bool:
    """Whether the surface is promoted (only an explicit ``on`` enables it)."""
    return read_surface_default(registry_path, surface) == ON


def require_surface(
    registry_path: Optional[Path] = None,
    surface: str = SURFACE_KEY,
) -> None:
    """Raise :class:`SurfaceDisabled` unless the surface is explicitly promoted."""
    if not surface_enabled(registry_path, surface):
        path = registry_path if registry_path is not None else DEFAULT_REGISTRY_PATH
        raise SurfaceDisabled(
            f"the conversational surface is not promoted: surfaces.{surface} in "
            f"{os.path.relpath(str(path), str(REPO_ROOT))} is not 'on' "
            "(flag checked before AuthN: an unpromoted surface is absent)"
        )
