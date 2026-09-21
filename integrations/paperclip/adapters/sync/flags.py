"""paperclip.adapters.sync.flags — the ``enable_paperclip`` gate reader (issue #1649).

Mirrors ``gateway/providers/flags.py`` (the ``enable_hermes`` reader): the
registry (``infra/feature-flags/registry.yaml``) is the declaration, this is
the reader, fail-closed. Never imports the hermes/gateway reader — each
flag-gated surface reads the declaration itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:  # pragma: no cover - the repo declares PyYAML
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(f"paperclip.adapters.sync: missing dependency ({exc}); need PyYAML") from exc

#: this file -> sync -> adapters -> paperclip -> integrations -> repository root
REPO_ROOT = Path(__file__).resolve().parents[5]

DEFAULT_REGISTRY_PATH = REPO_ROOT / "infra" / "feature-flags" / "registry.yaml"

#: The service key that owns the paperclip runtime in the registry's ``services:`` block.
SERVICE_KEY = "paperclip"

OFF = "off"
ON = "on"


def read_paperclip_default(registry_path: Optional[Path] = None) -> str:
    """The declared default for the paperclip service: ``"off"`` / ``"on"`` / ``""``.

    An absent document, entry or value returns ``""`` — the caller treats that
    as off (fail closed): "we could not read the declaration" must never mean
    "it is on".
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
    # boolean and the string form mean OFF.
    value = entry.get("default")
    if value is False or value == OFF:
        return OFF
    if value is True or value == ON:
        return ON
    return ""


def paperclip_enabled(registry_path: Optional[Path] = None) -> bool:
    """``True`` only when the declared default is explicitly ``on``."""
    return read_paperclip_default(registry_path) == ON
