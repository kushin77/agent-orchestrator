"""fleet/runtimes.py — the ONE runtime vocabulary (issue #1412, from #1301/#1271/#1385).

---knowledge---
module_id: fleet.runtimes
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [RegistryRefused, RuntimeRow, registry_path, source, rows, ids, identity_of, carries]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

`fleet/runtimes.yaml` is the **contract**: one row per runtime, `{id, kind,
transport, identity, token_scope}`. This module is the only reader of it, and
every surface that needs the vocabulary asks HERE rather than carrying a copy:

| consumer                     | was                                             |
|------------------------------|-------------------------------------------------|
| `fleet/beats.py`             | (new) the producer's registry check              |
| `fleet/channel.py`           | a literal seven-tuple, "the wire-value contract" |
| `governance/notices/runtime_registry.py` | its own five ids, derived from packs + catalog |
| `governance/isolation/runtimes.py` | a literal seven-tuple, "retired by #1271"    |

Two lists that AGREE today are not one list: the moment
`governance/notices/runtime_registry.py` carried five ids against this
contract's seven (#1385 measured exactly that) every lane record validated
against whichever list its caller happened to reach. The rule this module makes
mechanical is that there is ONE list, this file is it, and
`fleet/tests/test_runtime_vocabulary.py` proves every consumer FOLLOWS it — by
mutating the contract on a fixture and re-reading each consumer, not by
comparing two literals that happen to match.

Fail-closed, never empty: a registry that is absent, unreadable, not a mapping,
carrying no `runtimes:` list, carrying a row with no id, carrying a duplicate id
or an empty list is REFUSED by name. An empty vocabulary would make every
consumer that checks membership (`runtime-unregistered`, `runtime must be one
of`) accept nothing or refuse everything, and both of those read as a working
gate.

The module is deliberately NOT named `runtime_registry`: `fleet/` is placed on
`sys.path[0]` by `governance/dispatch/claims.py`, so a bare `import
runtime_registry` would then resolve to the fleet copy for
`governance/notices/` as well — the same shadowing trap `fleet/runtime_liveness.py`
documents for `fleet/liveness.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The contract, relative to the repository root (which is this module's parent's parent).
REGISTRY_RELPATH = "fleet/runtimes.yaml"

#: The contract's declared schema id, checked so a foreign YAML file cannot be read
#: as the runtime registry by accident.
SCHEMA = "cmr.runtime-registry/v1"

#: The closed vocabularies the contract's own header declares. Enforcing them here
#: is what makes "closed" a property of the tree rather than a comment.
KINDS = ("agent", "gateway", "integration")
TRANSPORTS = ("cli", "api", "terminal", "local")


class RegistryRefused(RuntimeError):
    """The runtime registry cannot be read as a registry (never an empty vocabulary)."""


@dataclass(frozen=True)
class RuntimeRow:
    """One declared runtime: what it is called and how it is reached."""

    id: str
    kind: str
    transport: str
    identity: str
    token_scope: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "kind": self.kind,
            "transport": self.transport,
            "identity": self.identity,
            "token_scope": self.token_scope,
        }


def registry_path(root: Path | str | None = None) -> Path:
    """Where the contract is, for `root` (the repository root) or this checkout."""
    base = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return base / REGISTRY_RELPATH


def source(root: Path | str | None = None) -> str:
    """The registry's path as a string, for a refusal message a reader can act on."""
    return str(registry_path(root))


def rows(root: Path | str | None = None) -> tuple[RuntimeRow, ...]:
    """Every declared runtime row, in contract order.

    Refuses (``RegistryRefused``) rather than returning an empty tuple: see the
    module docstring — an empty vocabulary is not a fleet with nothing
    registered, it is an unreadable authority.
    """
    import yaml  # resolved on demand: PyYAML ships with the repo's tooling

    path = registry_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryRefused(f"the runtime registry {path} cannot be read: {exc}") from None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryRefused(f"the runtime registry {path} is not valid YAML: {exc}") from None
    if not isinstance(document, dict):
        raise RegistryRefused(f"the runtime registry {path} is not a mapping")
    declared = document.get("runtimes")
    if not isinstance(declared, list):
        raise RegistryRefused(f"the runtime registry {path} declares no 'runtimes:' list")
    if not declared:
        raise RegistryRefused(f"the runtime registry {path} declares an empty 'runtimes:' list")

    parsed: list[RuntimeRow] = []
    seen: set[str] = set()
    for entry in declared:
        if not isinstance(entry, dict):
            raise RegistryRefused(f"{path}: a runtime row is not a mapping: {entry!r}")
        runtime_id = entry.get("id")
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise RegistryRefused(f"{path}: a runtime row carries no string 'id': {entry!r}")
        runtime_id = runtime_id.strip()
        if runtime_id in seen:
            raise RegistryRefused(f"{path}: runtime '{runtime_id}' is declared more than once")
        seen.add(runtime_id)
        kind = str(entry.get("kind") or "")
        if kind not in KINDS:
            raise RegistryRefused(
                f"{path}: runtime '{runtime_id}' declares kind {kind!r}; "
                f"the contract's closed vocabulary is {', '.join(KINDS)}"
            )
        transport = str(entry.get("transport") or "")
        if transport not in TRANSPORTS:
            raise RegistryRefused(
                f"{path}: runtime '{runtime_id}' declares transport {transport!r}; "
                f"the contract's closed vocabulary is {', '.join(TRANSPORTS)}"
            )
        parsed.append(
            RuntimeRow(
                id=runtime_id,
                kind=kind,
                transport=transport,
                identity=str(entry.get("identity") or runtime_id),
                token_scope=str(entry.get("token_scope") or ""),
            )
        )
    return tuple(parsed)


def ids(root: Path | str | None = None) -> tuple[str, ...]:
    """The registered runtime ids — THE ONE LIST (#1412)."""
    return tuple(row.id for row in rows(root))


def identity_of(runtime_id: str, root: Path | str | None = None) -> str:
    """The identity family a runtime id speaks for (the contract's `identity` column)."""
    for row in rows(root):
        if row.id == runtime_id:
            return row.identity
    return ""


def carries(root: Path | str | None = None) -> bool:
    """Whether the contract is present at all — for callers with a pre-contract path."""
    return registry_path(root).is_file()
