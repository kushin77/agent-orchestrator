"""registry/profiles/tiers.py — THE model-tier vocabulary reader (issue #1494).

``registry/profiles/catalog.yaml`` ``tiers:`` is the **declared authority** for
the platform's model-tier ids — the ladder the gateway routes on, the registry
profile contract carries, the identity overlay validates, the portal renders and
the hermes projection emits. This module is that authority's one reader: every
surface that needs the vocabulary asks HERE rather than carrying a copy of it.

| consumer                           | was                                                  |
|------------------------------------|------------------------------------------------------|
| ``gateway/providers/contract.py``  | ``TIERS = ("LOW", "MED", "HIGH", "MAX")``             |
| ``governance/authority/model.py``  | the same tuple, plus its own ``TIER_RANK``            |
| ``identity/onboarding/model.py``   | the same tuple, as ``MODEL_TIERS``                    |
| ``integrations/hermes/mapping.py`` | the same tuple, as ``MODEL_TIERS``                    |
| ``portal/server/chat.py``          | the same tuple, "pinned from the gateway's contract"  |

Five tuples that AGREE today are not one vocabulary: nothing failed when one of
them drifted, which is the whole point of a declared authority. The rule this
module makes mechanical is that there is ONE ladder, ``catalog.yaml`` declares
it, and ``scripts/check-tier-vocabulary.sh`` refuses a sixth copy — by name, and
provoked both ways (a planted copy is refused; the clean tree passes).

Fail-closed, never empty: an authority that is absent, unreadable, not a
mapping, declaring no ``tiers:`` mapping, declaring an EMPTY one, declaring a
duplicate tier or a tier whose key is not a non-empty string is REFUSED by name.
An empty vocabulary would make every consumer that checks membership accept
nothing or refuse everything, and both of those read as a working gate.

The boundaries this reader does NOT cross are recorded in the authority's own
header, so "which list is the source" is never a question:

* ``governance/cto-overlay/overlay.py`` declares ``TIERS`` as ``experimental /
  standard / critical`` — **evidence** tiers, a different concept, out of scope.
* the FinOps tier NAMES (``flash`` / ``pro`` / ``auditor``) are declared by
  ``governance/finops/policy.json`` and read through ``governance/finops/
  chooser.py``; this reader never restates them.
* ``gateway/finops/tiers.yaml`` carries the L0/L1/L2 cheapest-capable LADDER and
  maps it onto these ids (``registryTier``); it consumes, never redefines.
* ``registry/profiles/validate.py`` derives the catalog's whole closed-vocabulary
  set (tools, capabilities, constraints, tiers, memory scopes, guardrail
  policies) for the schema<->catalog parity gate. That is a VALIDATOR of the
  catalog, not a second authority for the tier ladder, and it deliberately keeps
  reading the whole file it validates.

PyYAML is imported ON DEMAND (as ``fleet/runtimes.py`` does): importing this
module must never itself require the parser, so a consumer stays importable in a
scratch tree that carries no YAML tooling.
"""

from __future__ import annotations

from pathlib import Path

#: The authority, relative to the repository root (this module's grandparent).
CATALOG_RELPATH = "registry/profiles/catalog.yaml"

#: The key inside the authority that holds the vocabulary.
VOCABULARY = "tiers"


class TierVocabularyRefused(RuntimeError):
    """The authority cannot be read as a tier vocabulary (never an empty ladder)."""


#: Read-once cache, keyed by the authority path, so a per-message consumer
#: (``fleet/channel.py`` validates every directive) does not re-parse YAML.
_CACHE: dict[Path, tuple[str, ...]] = {}


def catalog_path(root: Path | str | None = None) -> Path:
    """Where the authority is, for ``root`` (the repository root) or this checkout."""
    base = Path(root) if root is not None else Path(__file__).resolve().parent.parent.parent
    return base / CATALOG_RELPATH


def source(root: Path | str | None = None) -> str:
    """The authority's path as a string, for a refusal a reader can act on."""
    return str(catalog_path(root))


def carries(root: Path | str | None = None) -> bool:
    """Whether the authority is present at all — for callers with a pre-authority path."""
    return catalog_path(root).is_file()


def clear_cache() -> None:
    """Drop the read-once cache (tests that re-read a rewritten authority)."""
    _CACHE.clear()


def _declared_keys(text: str, path: Path) -> list[str]:
    """The ``tiers:`` keys AS WRITTEN, in file order.

    Read from the YAML node tree rather than from a parsed mapping, because a
    duplicate key is silently merged by the parser and a vocabulary that declares
    ``HIGH`` twice is a defect that must be named, not swallowed.
    """
    import yaml  # resolved on demand, exactly like fleet/runtimes.py

    try:
        document = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise TierVocabularyRefused(f"the tier authority {path} is not valid YAML: {exc}") from None
    if not isinstance(document, yaml.MappingNode):
        raise TierVocabularyRefused(f"the tier authority {path} is not a mapping")
    for key_node, value_node in document.value:
        if getattr(key_node, "value", None) != VOCABULARY or not isinstance(key_node, yaml.ScalarNode):
            continue
        if not isinstance(value_node, yaml.MappingNode):
            raise TierVocabularyRefused(
                f"the tier authority {path} declares a {VOCABULARY!r} that is not a mapping"
            )
        keys: list[str] = []
        for sub_key, _sub_value in value_node.value:
            if not isinstance(sub_key, yaml.ScalarNode) or not isinstance(sub_key.value, str):
                raise TierVocabularyRefused(
                    f"{path}: a {VOCABULARY!r} key is not a string: {sub_key!r}"
                )
            keys.append(sub_key.value)
        return keys
    raise TierVocabularyRefused(f"the tier authority {path} declares no {VOCABULARY!r} mapping")


def authority(root: Path | str | None = None) -> tuple[str, ...]:
    """The declared tier ids, in authority order — THE ONE LADDER (#1494).

    Refuses (``TierVocabularyRefused``) rather than returning an empty tuple: an
    empty vocabulary is not a platform with no tiers, it is an unreadable
    authority, and every membership check downstream would then judge by accident.
    """
    path = catalog_path(root)
    if path in _CACHE:
        return _CACHE[path]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TierVocabularyRefused(f"the tier authority {path} cannot be read: {exc}") from None
    keys = _declared_keys(text, path)
    if not keys:
        raise TierVocabularyRefused(
            f"the tier authority {path} declares an empty {VOCABULARY!r} mapping"
        )
    seen: set[str] = set()
    for key in keys:
        if not key.strip():
            raise TierVocabularyRefused(f"{path}: a {VOCABULARY!r} key is blank")
        if key in seen:
            raise TierVocabularyRefused(f"{path}: tier {key!r} is declared more than once")
        seen.add(key)
    ladder = tuple(keys)
    _CACHE[path] = ladder
    return ladder


def rank(root: Path | str | None = None) -> dict[str, int]:
    """``{tier id: rank}`` — rank is the authority's declaration order (cheapest first)."""
    return {tier: index for index, tier in enumerate(authority(root))}
