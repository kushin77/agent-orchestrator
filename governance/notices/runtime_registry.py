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

THE VOCABULARY HAS ONE AUTHORITY (issue #1412, from #1385)
---------------------------------------------------------
This module used to carry a list of its own: it derived five ids (``claude``,
``deepseek``, ``hermes``, ``ollama``, ``paperclip``) as "a live AgentPack identity
the gateway catalog carries a transport for", while the contract
``fleet/runtimes.yaml`` declared SEVEN runtime ids (``claude-session``,
``claude-subagent``, ``deepseek-sister``, ``deepseek-executor``,
``copilot-agent``, ``hermes``, ``paperclip``). Two sets that agree today are not
one set -- and they did not even agree: #1385 measured the mismatch, and every
lane record's ``runtime`` was validated against whichever list its caller happened
to reach.

So the set is now READ from the contract, through the one loader
(``fleet/runtimes.py``, the module every consumer reads): the ids are
``fleet/runtimes.yaml``'s rows, the provider is whatever the gateway catalog
carries for the row's declared ``identity``, and the fanout is the runtime's own
adapter CLI when the tree has one and the mailbox otherwise. An identity that a
live pack bundles but the contract does not register is a ROLE here (it owes no
ack) -- and ``contract_gaps`` names the ones the catalog could carry, so the
demotion is reported rather than silent.

A tree with NO contract at all (a fixture built before #1376, or a scratch copy)
keeps the pre-contract DERIVATION: live-pack identities the catalog carries. That
is not a second vocabulary: an id list is only ever the contract's, and the
pre-contract path computes a set rather than restating one.

A registered identity is a RUNTIME when the gateway carries a transport for it,
and a ROLE otherwise. Measured on this tree before the reconciliation: ``claude``,
``deepseek``, ``hermes``, ``ollama`` and ``paperclip`` were runtimes; ``coder``,
``data-agent`` and ``orchestrator`` were roles -- agent identities the fleet
dispatches ON, not execution surfaces that can acknowledge anything. The
consequence is the property the rule needs: adding a runtime is a REGISTRATION ACT
(a row in the contract) and, the moment it lands, every standing notice requires
its ack. No notice is edited and no list is maintained.

Exit-code-free by design: this is a library. ``RegistryUnavailable`` is raised when
an input cannot be read, so a caller turns that into CANNOT-ASSESS -- never into an
empty set, which would make every notice trivially satisfied.
"""

from __future__ import annotations

import json
import sys
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
#: The ONE runtime authority (issue #1412): the contract `fleet/runtimes.yaml`,
#: read through the one loader `fleet/runtimes.py`.
RUNTIME_CONTRACT_RELPATH = "fleet/runtimes.yaml"
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
    """The registered runtimes: the CONTRACT's rows when the tree carries one (#1412).

    The contract is ``fleet/runtimes.yaml``, read through ``fleet/runtimes.py`` --
    the one loader every consumer shares, so this module cannot carry a vocabulary
    of its own. A tree without a contract keeps the pre-contract derivation
    (``_derived_runtimes``): live-pack identities the gateway carries.
    """
    rows = _contract_rows(root)
    if rows is not None:
        return tuple(_runtime_from_contract(root, row) for row in rows)
    return _derived_runtimes(root)


def _runtime_from_contract(root: Path | str, row: Any) -> "Runtime":
    """One contract row as a Runtime: its own id, the transport the tree carries.

    The ``provider`` is what the gateway catalog holds for the identity the row
    declares (``claude`` for both Claude rungs, ``deepseek`` for both DeepSeek
    rungs). A contract row whose identity no catalog module carries keeps its
    identity as the provider and is still a registered runtime: the contract, not
    the catalog, decides who must ack.
    """
    provider = catalog_identities(root).get(row.identity) or row.identity
    return Runtime(
        id=row.id,
        provider=provider,
        fanout=fanout_path(root, row.id),
        transport=transport_name(root, row.id),
    )


def _contract_rows(root: Path | str) -> tuple[Any, ...] | None:
    """The contract's rows, or None when this tree carries no contract.

    A contract that EXISTS but cannot be read is ``RegistryUnavailable`` (never
    "no contract"): falling back to the pre-contract derivation there would answer
    a broken authority with a stale one.
    """
    contract = Path(root) / RUNTIME_CONTRACT_RELPATH
    if not contract.is_file():
        return None
    module = _runtime_contract_module()
    try:
        return module.rows(Path(root))
    except module.RegistryRefused as exc:
        raise RegistryUnavailable(
            "the runtime contract %s is present but cannot be read as one: %s"
            % (RUNTIME_CONTRACT_RELPATH, exc)
        ) from exc


def _runtime_contract_module() -> Any:
    """``fleet.runtimes`` -- the one loader, imported with the repo root on the path.

    Lazy, and path-repaired: these modules are executed both as a package
    (``python3 governance/notices/cli.py``, where ``sys.path[0]`` is the notices
    directory) and under pytest, and the fleet loader lives at the repo root.
    """
    here = Path(__file__).resolve().parents[2]
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from fleet import runtimes as contract  # noqa: PLC0415 - see the docstring

    return contract


def contract_ids(root: Path | str) -> tuple[str, ...] | None:
    """The contract's ids, exactly as the fleet loader reads them (None: no contract).

    Public because it is what a vocabulary test compares consumers against: the
    point of the reconciliation is that every consumer FOLLOWS this list, and a
    test cannot prove that against a private helper.
    """
    rows = _contract_rows(root)
    return None if rows is None else tuple(row.id for row in rows)


def contract_identities(root: Path | str) -> tuple[str, ...]:
    """The identity families the contract's rows speak for (`claude`, `deepseek`, ...)."""
    rows = _contract_rows(root)
    return () if rows is None else tuple(sorted({row.identity for row in rows}))


def contract_gaps(root: Path | str) -> tuple[str, ...]:
    """Identities the PRE-contract rule would have called runtimes, which the contract does not register.

    Reported, never silently absorbed: after #1412 the contract is the vocabulary,
    so such an identity owes no ack here -- and this function names it, so the
    demotion is visible instead of being discovered by a runtime that stopped
    receiving notices. Measured on this tree: ``ollama`` is bundled by a live pack
    and carried by the gateway catalog, and no ``fleet/runtimes.yaml`` row registers
    it, so it is a role until a row says otherwise.
    """
    rows = _contract_rows(root)
    if rows is None:
        return ()
    registered: set[str] = set()
    for row in rows:
        registered.add(row.id)
        registered.add(row.identity)
    carried = catalog_identities(root)
    return tuple(
        identity
        for identity in live_pack_profiles(root)
        if identity not in registered and identity in carried
    )


def _derived_runtimes(root: Path | str) -> tuple[Runtime, ...]:
    """The PRE-contract derivation: live-pack identities the gateway carries.

    Kept for fixtures and scratch trees that have no ``fleet/runtimes.yaml``
    (``scripts/check-notice-acks.sh`` builds one to prove a role owes no ack). It
    computes a set from two declarations the tree already owns; it does not restate
    an id list, and it is never consulted when the contract is present.
    """
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
    """Live-pack identities with no gateway transport: agent ROLES, not runtimes.

    An identity the contract names as some runtime's ``identity`` is not a role
    either (#1412): ``claude`` is what two registered rungs speak for, so it is
    covered by them rather than owing an ack of its own. With no contract in the
    tree this is the pre-contract rule exactly as it was.
    """
    runtime_ids = {runtime.id for runtime in registered_runtimes(root)}
    covered = runtime_ids | set(contract_identities(root))
    return tuple(
        identity for identity in live_pack_profiles(root) if identity not in covered
    )


def describe(root: Path | str) -> dict[str, Any]:
    """The derivation's own report: what it read, and what it derived from it."""
    runtimes = registered_runtimes(root)
    roles = unregistered_profiles(root)
    contract = contract_ids(root)
    return {
        "root": str(root),
        # Which authority answered, named: a report that does not say whether it
        # read the contract or the pre-contract derivation cannot be audited.
        "authority": RUNTIME_CONTRACT_RELPATH if contract is not None else "pre-contract derivation",
        "contract_ids": list(contract) if contract is not None else [],
        "contract_gaps": list(contract_gaps(root)),
        "pack_releases": [str(path) for path in pack_release_paths(root)],
        "runtimes": [runtime.as_dict() for runtime in runtimes],
        "roles": list(roles),
    }
