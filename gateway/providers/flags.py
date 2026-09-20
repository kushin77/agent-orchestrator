"""gateway.providers.flags — the ``enable_hermes`` gate reader (issue #1518).

The registry (``infra/feature-flags/registry.yaml``) is the declaration; this
module is the reader. Mirrors ``gateway/chat/flags.py``: the ``services.hermes``
entry is read fail-closed, so a missing, unreadable, or absent entry leaves the
provider **off**. Promotion is a reviewed edit to the declared registry, never a
runtime override.

The reader is deliberately small and dependency-light (PyYAML only) because it
runs on every registry construction, and it never imports the chat surface's own
reader: each flag-gated surface reads the single declaration itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:  # pragma: no cover - the repo declares PyYAML
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(f"gateway.providers: missing dependency ({exc}); need PyYAML") from exc

#: gateway/providers/flags.py -> gateway/providers -> gateway -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The registry document the hermes provider is declared in.
DEFAULT_REGISTRY_PATH = REPO_ROOT / "infra" / "feature-flags" / "registry.yaml"

#: The service key that owns the hermes provider in the registry's ``services:`` block.
SERVICE_KEY = "hermes"

OFF = "off"
ON = "on"


def read_hermes_default(
    registry_path: Optional[Path] = None,
) -> str:
    """The declared default for the hermes service: ``"off"`` / ``"on"`` / ``""``.

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
    services = document.get("services")
    if not isinstance(services, dict):
        return ""
    entry = services.get(SERVICE_KEY)
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


def hermes_enabled(
    registry_path: Optional[Path] = None,
) -> bool:
    """Whether the hermes provider is promoted (only an explicit ``on`` enables it)."""
    return read_hermes_default(registry_path) == ON
