"""The runtime vocabulary a lane record's ``runtime`` field is validated against (#1301).

A lane is bound to one lane at creation whatever runtime opened it — a Claude
session, a Claude subagent, the DeepSeek sister or executor, a Copilot agent,
hermes, paperclip — and the record names that runtime as a registry id, never as
a runtime-shaped field. The registry is ``fleet/runtimes.yaml`` (issue #1301's
runtime-agnostic comment: ``{id, kind, transport, identity, token_scope}`` per
row, "adding a runtime is a row plus an adapter, never a code branch"). That
file is another lane's to create (#1271); until it lands this module carries the
seven ids the contract names as a literal, so the refusal exists today and the
list is replaced by the registry the moment it is present.

``runtime-unregistered:<id>`` is the refusal: a record naming a runtime the
registry does not carry is refused by ``open`` before anything is created, and
an artifact created by an unregistered actor is an orphan whichever model made
it. An EMPTY runtime is not refused here — the callers that mint lanes today
(``fleet/terminal.py``, ``governance/spawn/cli.py``) do not pass one yet, and
refusing them would stop every dispatch; ``open`` names it ``runtime-unrecorded``
in its payload instead, and the reconcile sweep treats such a lane as owed a
runtime, never as belonging to one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

#: The registry file, relative to the repository root. Owned by #1271.
REGISTRY_PATH = "fleet/runtimes.yaml"

#: Retired by #1271: delete this literal once `fleet/runtimes.yaml` lands; the
#: registry is then the only authority and this module only reads it. The seven
#: ids are the ones issue #1301's runtime-agnostic comment names today.
FALLBACK_RUNTIME_IDS: tuple[str, ...] = (
    "claude-session",
    "claude-subagent",
    "deepseek-sister",
    "deepseek-executor",
    "copilot-agent",
    "hermes",
    "paperclip",
)

RUNTIME_UNREGISTERED = "runtime-unregistered"
RUNTIME_UNRECORDED = "runtime-unrecorded"


class RegistryUnreadable(RuntimeError):
    """``fleet/runtimes.yaml`` exists but cannot be read as a registry."""


def _ids_from_registry(path: Path) -> tuple[str, ...]:
    try:
        import yaml  # noqa: PLC0415 - optional at import time
    except ImportError as exc:  # pragma: no cover - PyYAML ships with the repo's tooling
        raise RegistryUnreadable(f"{path} exists but PyYAML is not importable: {exc}") from exc
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryUnreadable(f"{path} could not be read as YAML: {exc}") from exc
    rows = document.get("runtimes") if isinstance(document, dict) else document
    if not isinstance(rows, list) or not rows:
        raise RegistryUnreadable(f"{path} declares no `runtimes:` rows")
    ids: list[str] = []
    for row in rows:
        identifier = row.get("id") if isinstance(row, dict) else row
        if not isinstance(identifier, str) or not identifier.strip():
            raise RegistryUnreadable(f"{path}: a runtime row carries no string `id`")
        ids.append(identifier.strip())
    if len(set(ids)) != len(ids):
        raise RegistryUnreadable(f"{path}: duplicate runtime ids")
    return tuple(ids)


def runtime_ids(root: Path | str | None = None) -> tuple[str, ...]:
    """The registered runtime ids: the registry's rows when it exists, else the literal.

    ``root`` is the repository root; ``None`` means this checkout.
    """
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    registry = base / REGISTRY_PATH
    if registry.exists():
        return _ids_from_registry(registry)
    return FALLBACK_RUNTIME_IDS


def registry_source(root: Path | str | None = None) -> str:
    """Where the vocabulary came from, for a refusal message."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    return REGISTRY_PATH if (base / REGISTRY_PATH).exists() else f"the literal list in {__name__} (pending #1271)"


def unregistered(runtime: str, root: Path | str | None = None, ids: Iterable[str] | None = None) -> str:
    """The ``runtime-unregistered`` refusal for ``runtime``, or ``""`` when it is registered.

    An empty runtime is NOT unregistered (see the module docstring); callers
    name it ``runtime-unrecorded`` themselves.
    """
    if not runtime:
        return ""
    known = tuple(ids) if ids is not None else runtime_ids(root)
    if runtime in known:
        return ""
    return (
        f"{RUNTIME_UNREGISTERED}:{runtime}: {registry_source(root)} carries "
        f"{', '.join(known)}; a lane naming any other runtime owes no record and its "
        "record proves nothing — register the runtime first (#1271)"
    )
