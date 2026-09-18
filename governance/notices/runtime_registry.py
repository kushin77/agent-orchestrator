"""The registered-runtime set -- DERIVED from the repository's registry, never typed.

THE RULE THIS FILE MAKES MECHANICAL (issue #1269, EPIC #1268)
------------------------------------------------------------
A standing notice must be acked by EVERY REGISTERED RUNTIME. The load-bearing word
is "registered", because the failure the rule exists for is a rule that was
broadcast and never received: on 2026-09-18 the merge-path rules went out both
ways -- a SendMessage broadcast to the Claude sessions and a hand-built mailbox
directive to the DeepSeek sister -- and zero acks were recorded. A hand-written
list of who must ack reproduces that failure one step later: the list is written
once, a runtime is added afterwards, and the notice silently stops covering it.

So the set is COMPUTED, from two declarations the repository already owns:

  1. ``registry/packs/releases/*.yaml`` -- the AgentPack release snapshots. They
     are the registry of REGISTERED AGENT IDENTITIES: a pack bundles profiles by
     ``id@version`` (``contents.profile[].ref``) and only a pack whose
     ``lifecycle`` is ``live`` registers anything.
  2. ``gateway/catalog/modules/*/module.json`` -- the gateway catalog. A module
     names the identities and the provider that carries them: its
     ``distribution.package`` (``gateway.providers.<provider>``) is the transport,
     and its ``class`` list names the identities riding it (``claude-anthropic``
     names ``claude``, which is why no claude->anthropic alias table is needed).

A registered identity is a RUNTIME when the gateway carries a transport for it,
and a ROLE otherwise. Measured on this tree: ``claude``, ``deepseek``, ``hermes``,
``ollama`` and ``paperclip`` are runtimes; ``coder``, ``data-agent`` and
``orchestrator`` are roles -- agent identities the fleet dispatches ON, not
execution surfaces that can acknowledge anything. Nobody classified those by
hand; the intersection did.

The consequence is the property the rule needs: adding a runtime is a
REGISTRATION ACT (publish a live pack bundling it) and, the moment it lands, every
standing notice requires its ack. No notice is edited and no list is maintained.

Exit-code-free by design: this is a library. ``RegistryUnavailable`` is raised when
an input cannot be read, so a caller turns that into CANNOT-ASSESS -- never into an
empty set, which would make every notice trivially satisfied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # The pack snapshots are YAML; its absence is CANNOT-ASSESS, never "empty".
    import yaml
except ImportError:  # pragma: no cover - exercised by the caller's guard
    yaml = None  # type: ignore[assignment]


class RegistryUnavailable(RuntimeError):
    """An input the derivation needs cannot be read (CANNOT-ASSESS, never OK)."""


LIVE_PACKS_RELPATH = "registry/packs/releases"
CATALOG_MODULES_RELPATH = "gateway/catalog/modules"
LIFECYCLE_FIELD = "lifecycle"
LIVE_LIFECYCLE = "live"
CONTENTS_FIELD = "contents"
PROFILE_ARTIFACT = "profile"
PACKAGE_PREFIX = "gateway.providers."

#: The transport of record is the file mailbox (ADR-0011), so every fleet runtime
#: has one: the mailbox is the fan-out fallback for a runtime with no adapter CLI.
MAILBOX_RELPATH = "fleet/channel.py"
ADAPTER_CLI_TEMPLATE = "integrations/{runtime}/cli.py"


@dataclass(frozen=True)
class Runtime:
    """One registered runtime: what it is called, how it is carried, where its copy goes."""

    id: str
    provider: str
    fanout: str
    transport: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "provider": self.provider,
            "transport": self.transport,
            "fanout": self.fanout,
        }


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RegistryUnavailable(
            "PyYAML is not importable, so %s cannot be read (the pack registry is YAML)"
            % path
        )
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except (OSError, ValueError) as exc:
        raise RegistryUnavailable("%s cannot be read: %s" % (path, exc)) from exc


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise RegistryUnavailable("%s cannot be read: %s" % (path, exc)) from exc


def pack_release_paths(root: Path | str) -> tuple[Path, ...]:
    """Every AgentPack release snapshot, in a stable order.

    A packs directory with no release snapshot raises rather than returning an
    empty tuple: an empty registry is not a fleet with nothing registered, and a
    silent empty set here would make every standing notice trivially satisfied.
    """
    packs_dir = Path(root) / LIVE_PACKS_RELPATH
    if not packs_dir.is_dir():
        raise RegistryUnavailable(
            "%s is missing -- the pack registry is the registration authority, "
            "so without it the set of runtimes a notice must reach is unknown"
            % LIVE_PACKS_RELPATH
        )
    releases = tuple(sorted(packs_dir.glob("*.yaml")))
    if not releases:
        raise RegistryUnavailable(
            "%s holds no release snapshot -- a registered fleet always has at "
            "least one, so this tree's registry cannot be assessed" % LIVE_PACKS_RELPATH
        )
    return releases


def live_pack_profiles(root: Path | str) -> tuple[str, ...]:
    """The profile ids bundled by every LIVE AgentPack (the registered identities)."""
    identities: set[str] = set()
    for path in pack_release_paths(root):
        document = _load_yaml(path)
        if not isinstance(document, Mapping):
            raise RegistryUnavailable("%s is not a mapping" % path)
        if str(document.get(LIFECYCLE_FIELD) or "") != LIVE_LIFECYCLE:
            continue
        contents = document.get(CONTENTS_FIELD) or {}
        if not isinstance(contents, Mapping):
            raise RegistryUnavailable("%s: %s is not a mapping" % (path, CONTENTS_FIELD))
        for entry in contents.get(PROFILE_ARTIFACT) or []:
            if not isinstance(entry, Mapping):
                continue
            reference = str(entry.get("ref") or "")
            name = reference.split("@", 1)[0].strip()
            if name:
                identities.add(name)
    return tuple(sorted(identities))


def catalog_identities(root: Path | str) -> dict[str, str]:
    """Map every identity the gateway catalog names to the provider carrying it."""
    modules_dir = Path(root) / CATALOG_MODULES_RELPATH
    if not modules_dir.is_dir():
        raise RegistryUnavailable(
            "%s is missing -- without the catalog there is no transport to derive a "
            "runtime from" % CATALOG_MODULES_RELPATH
        )
    identities: dict[str, str] = {}
    for manifest in sorted(modules_dir.glob("*/module.json")):
        document = _load_json(manifest)
        if not isinstance(document, Mapping):
            raise RegistryUnavailable("%s is not a mapping" % manifest)
        distribution = document.get("distribution") or {}
        package = str(distribution.get("package") or "") if isinstance(distribution, Mapping) else ""
        if package.startswith(PACKAGE_PREFIX):
            provider = package[len(PACKAGE_PREFIX):].strip()
        else:
            provider = str(document.get("id") or "").strip()
        names = {str(document.get("id") or ""), provider}
        classes = document.get("class") or []
        if isinstance(classes, list):
            names.update(str(item) for item in classes)
        for name in names:
            if name:
                identities.setdefault(name, provider)
    return identities


def fanout_path(root: Path | str, runtime_id: str) -> str:
    """The path this runtime's copy of a notice is written for (relative to root)."""
    root_path = Path(root)
    adapter = ADAPTER_CLI_TEMPLATE.format(runtime=runtime_id)
    if (root_path / adapter).is_file():
        return adapter
    return MAILBOX_RELPATH


def transport_name(root: Path | str, runtime_id: str) -> str:
    """The transport that carries this runtime's copy, by name."""
    fanout = fanout_path(root, runtime_id)
    if fanout == MAILBOX_RELPATH:
        return "mailbox:%s" % MAILBOX_RELPATH
    return "adapter:%s" % fanout


def registered_runtimes(root: Path | str) -> tuple[Runtime, ...]:
    """The registered runtimes, derived: live-pack identities the gateway carries."""
    identities = catalog_identities(root)
    runtimes = []
    for runtime_id in live_pack_profiles(root):
        provider = identities.get(runtime_id)
        if not provider:
            continue
        runtimes.append(
            Runtime(
                id=runtime_id,
                provider=provider,
                fanout=fanout_path(root, runtime_id),
                transport=transport_name(root, runtime_id),
            )
        )
    return tuple(sorted(runtimes, key=lambda runtime: runtime.id))


def unregistered_profiles(root: Path | str) -> tuple[str, ...]:
    """Live-pack identities with no gateway transport: agent ROLES, not runtimes."""
    runtime_ids = {runtime.id for runtime in registered_runtimes(root)}
    return tuple(
        identity for identity in live_pack_profiles(root) if identity not in runtime_ids
    )


def describe(root: Path | str) -> dict[str, Any]:
    """The derivation's own report: what it read, and what it derived from it."""
    runtimes = registered_runtimes(root)
    roles = unregistered_profiles(root)
    return {
        "root": str(root),
        "pack_releases": [str(path) for path in pack_release_paths(root)],
        "runtimes": [runtime.as_dict() for runtime in runtimes],
        "roles": list(roles),
    }
