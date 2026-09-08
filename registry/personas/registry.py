#!/usr/bin/env python3
"""SME persona registry - tenant-scoped persona cards + lifecycle API.

The persona library (EPIC-00 issue #11): packaged persona cards (name,
expertise, owned lanes, default model tier, guardrails) that are tenant-
extensible and map onto AgentProfiles (issue #9). Design (see README.md for
the full contract):

- Persona cards live as YAML under ``cards/``, one file per persona
  (``cards/<id>.yaml``), validated against ``persona-card.schema.json``.
  **Filename adds no code change**: the loader discovers all cards by
  scanning the directory, so adding a file adds a persona.
- A persona belongs to a tenant (``tenant: <tenant-id>``) or to the platform
  default (``tenant: platform``). Resolution is tenant-first with a documented
  platform fallback - a tenant card shadows the platform card of the same id;
  there is never a fallback across other tenants.
- A persona becomes dispatchable only when **published**, which appends its
  ``(tenant, persona, version, file, sha256)`` to the append-only ledger
  ``versions/manifest.yaml``. Publishing freezes that version: editing a
  published card's content is an integrity violation (sha mismatch) until a
  new version is authored and published. ``retire`` marks a persona retired in
  the ledger; a retired persona no longer resolves.
- ``resolve(tenant, id)`` returns the published persona and **refuses**
  unpublished, retired, unknown or integrity-violated personas.

Consumed contracts (read-only, never edited here): the closed platform
vocabulary in ``registry/profiles/catalog.yaml`` and the AgentProfile schema
``registry/profiles/agent-profile.schema.json`` (issue #9). Validation is
two-layer - JSON Schema (embedded closed enums) plus fail-closed membership
against the live catalog - mirroring the issue #9 validator.

Usage (from the repo root):

    python3 registry/personas/registry.py status
    python3 registry/personas/registry.py validate cards/security-sme.yaml
    python3 registry/personas/registry.py resolve security-sme
    python3 registry/personas/registry.py resolve coder --tenant acme
    python3 registry/personas/registry.py publish orchestrator
    python3 registry/personas/registry.py retire orchestrator
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import jsonschema  # type: ignore
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.exit(f"personas: missing dependency ({exc}); need jsonschema + PyYAML")

PKG_DIR = Path(__file__).resolve().parent
REGISTRY_DIR = PKG_DIR.parent
CARDS_DIR = PKG_DIR / "cards"
VERSIONS_DIR = PKG_DIR / "versions"
MANIFEST_PATH = VERSIONS_DIR / "manifest.yaml"
CARD_SCHEMA_PATH = PKG_DIR / "persona-card.schema.json"
PROFILE_SCHEMA_PATH = REGISTRY_DIR / "profiles" / "agent-profile.schema.json"
CATALOG_PATH = REGISTRY_DIR / "profiles" / "catalog.yaml"

_MANIFEST_SCHEMA = "persona-manifest/v1"
_PLATFORM_TENANT = "platform"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class PersonaError(Exception):
    """Base error for the persona registry."""


class UnknownPersonaError(PersonaError):
    """Raised when a (tenant, persona) does not exist and has no platform default."""


class UnpublishedPersonaError(PersonaError):
    """Raised when a discovered persona has not been published yet."""


class RetiredPersonaError(PersonaError):
    """Raised when a persona is published but retired (no longer dispatchable)."""


class IntegrityError(PersonaError):
    """Raised when a published persona's content no longer matches its frozen digest."""


class AlreadyPublishedError(PersonaError):
    """Raised when a version was already published under a different digest."""


class InvalidCardError(PersonaError):
    """Raised when a persona card fails schema or catalog validation."""


# --------------------------------------------------------------------------- #
# vocabulary accessors (issue #9 catalog - read-only consumption)
# --------------------------------------------------------------------------- #


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_card_schema() -> Dict[str, Any]:
    return _load_json(CARD_SCHEMA_PATH)


def load_profile_schema() -> Dict[str, Any]:
    if not PROFILE_SCHEMA_PATH.exists():
        raise PersonaError(
            f"AgentProfile schema not found at {PROFILE_SCHEMA_PATH} "
            "(issue #9 contract; persona mapping consumes it read-only)"
        )
    return _load_json(PROFILE_SCHEMA_PATH)


def load_catalog() -> Dict[str, Any]:
    """Load the closed platform vocabulary from the issue #9 catalog.

    Returns a dict of id sets keyed by vocabulary section ('tools',
    'capabilities', 'constraints', 'guardrailPolicies', 'tiers',
    'memoryScopes'). Fail closed: an unreadable catalog is an error, not a
    silently-open validation pass.
    """
    if not CATALOG_PATH.exists():
        raise PersonaError(
            f"platform catalog not found at {CATALOG_PATH} "
            "(issue #9 contract; persona cards consume it read-only)"
        )
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    policies = set()
    atomics = (data.get("guardrailPolicies") or {}).get("atomics") or {}
    bundles = (data.get("guardrailPolicies") or {}).get("bundles") or {}
    policies.update(atomics.keys())
    policies.update(bundles.keys())

    return {
        "tools": set((data.get("tools") or {}).keys()),
        "capabilities": set((data.get("capabilities") or {}).keys()),
        "constraints": set((data.get("constraints") or {}).keys()),
        "guardrailPolicies": policies,
        "tiers": set((data.get("tiers") or {}).keys()),
        "memoryScopes": set((data.get("memoryScopes") or {}).keys()),
    }


# --------------------------------------------------------------------------- #
# card validation (two-layer: schema + live-catalog membership)
# --------------------------------------------------------------------------- #


def validate_card(
    card: Dict[str, Any],
    schema: Optional[Dict[str, Any]] = None,
    catalog: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate a persona-card dict.

    Layer 1 is the JSON Schema (embedded closed enums). Layer 2 is fail-closed
    membership against the LIVE issue #9 catalog, so a vocabulary id can never
    slip through because the schema snapshot went stale. Raises
    InvalidCardError with the first violation.
    """
    schema = schema or load_card_schema()
    try:
        jsonschema.validate(instance=card, schema=schema)
    except jsonschema.ValidationError as exc:
        raise InvalidCardError(
            f"persona card invalid against persona-card.schema.json: {exc.message}"
        ) from exc

    if not _ID_RE.match(card["id"]):
        raise InvalidCardError(f"persona id {card['id']!r} must match ^[a-z][a-z0-9-]*$")
    if card.get("tenant") == _PLATFORM_TENANT and card["id"] == _PLATFORM_TENANT:
        raise InvalidCardError("'platform' is a reserved tenant; it cannot be a persona id")

    catalog = catalog or load_catalog()
    _check_members(card, "toolAllowlist", catalog["tools"])
    _check_members(card, "capabilitySet", catalog["capabilities"])
    _check_members(card, "constraintSet", catalog["constraints"])
    _check_members(card, "memoryScope", catalog["memoryScopes"])
    tier = card.get("defaultModelTier")
    if tier not in catalog["tiers"]:
        raise InvalidCardError(
            f"unknown defaultModelTier {tier!r} (not in platform catalog tiers)"
        )
    policy = card.get("guardrailPolicyRef")
    if policy not in catalog["guardrailPolicies"]:
        raise InvalidCardError(
            f"unknown guardrailPolicyRef {policy!r} "
            "(not in platform catalog guardrail policies)"
        )


def _check_members(card: Dict[str, Any], field: str, allowed: set) -> None:
    for value in card.get(field, []):
        if value not in allowed:
            raise InvalidCardError(
                f"unknown {field} entry {value!r} (not in platform catalog)"
            )


def card_from_yaml(path: Path) -> Dict[str, Any]:
    """Load one card file and validate it."""
    try:
        with path.open(encoding="utf-8") as fh:
            card = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise InvalidCardError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(card, dict):
        raise InvalidCardError(f"{path}: card must be a YAML mapping")
    validate_card(card)
    if card["id"] != path.stem:
        raise InvalidCardError(
            f"{path}: card id {card['id']!r} must equal filename stem {path.stem!r}"
        )
    return card


# --------------------------------------------------------------------------- #
# append-only lifecycle ledger (versions/manifest.yaml)
# --------------------------------------------------------------------------- #


def _empty_manifest() -> Dict[str, Any]:
    return {"manifestSchema": _MANIFEST_SCHEMA, "entries": []}


def _read_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or MANIFEST_PATH
    if not path.exists():
        return _empty_manifest()
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict) or "entries" not in data:
        raise PersonaError(f"{path}: manifest is malformed (missing entries)")
    return data


def _write_manifest(manifest: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(manifest, fh, sort_keys=False)


def _key(tenant: str, persona: str) -> Tuple[str, str]:
    return (tenant, persona)


def _record_file(fpath: Path) -> str:
    """Ledger-friendly card reference: package-relative when possible, else the filename."""
    try:
        return str(fpath.relative_to(PKG_DIR))
    except ValueError:
        return fpath.name


# --------------------------------------------------------------------------- #
# PersonaRegistry
# --------------------------------------------------------------------------- #


class PersonaRegistry:
    """Tenant-scoped persona registry.

    A registry manages one persona library (a cards directory of one-file-per-
    persona YAML cards). For the shipped platform library, ``cards_dir`` is
    ``cards/`` and every card is ``tenant: platform``. A tenant's library is
    its own cards directory; when it is composed with the platform library
    (``platform_dir``) the registry resolves tenant-first with a platform
    fallback - so tenants inherit the platform persona set and extend/override
    it by adding their own card files (no cross-tenant leakage).

    ``manifest_path`` / ``catalog`` overrides let tests exercise lifecycle
    against a scratch registry without touching the committed seed ledger.
    """

    def __init__(
        self,
        cards_dir: Optional[Path] = None,
        platform_dir: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
        catalog: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.cards_dir = Path(cards_dir) if cards_dir else CARDS_DIR
        platform_dir = Path(platform_dir) if platform_dir else None
        # A registry whose managed dir IS the platform library has no separate
        # platform dir (scanning the same dir twice would double-register).
        if platform_dir is not None and platform_dir.resolve() == self.cards_dir.resolve():
            platform_dir = None
        self.platform_dir = platform_dir
        self.manifest_path = Path(manifest_path) if manifest_path else MANIFEST_PATH
        self._catalog = catalog
        self._schema = load_card_schema()

    # -- discovery -------------------------------------------------------- #
    @property
    def catalog(self) -> Dict[str, Any]:
        if self._catalog is None:
            self._catalog = load_catalog()
        return self._catalog

    @staticmethod
    def _scan(dirpath: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
        if not dirpath.exists():
            raise PersonaError(f"cards directory not found: {dirpath}")
        found: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for path in sorted(dirpath.glob("*.yaml")):
            card = card_from_yaml(path)
            key = _key(card["tenant"], card["id"])
            if key in found:
                raise PersonaError(f"duplicate persona card for {key}")
            found[key] = card
        return found

    def discover(self, refresh: bool = False) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Scan the cards directory (and, when set, the platform library) and
        register every card.

        One file per persona: ``cards/<id>.yaml``. Adding a file adds a
        persona with no code change. Always reflects disk state (no stale
        cache) so publish/retire/resolve see the latest card content.
        ``refresh`` is accepted for API compatibility. Returns
        {(tenant, id): card}.
        """
        found: Dict[Tuple[str, str], Dict[str, Any]] = {}
        found.update(self._scan(self.cards_dir))
        if self.platform_dir is not None:
            platform_cards = self._scan(self.platform_dir)
            for key in platform_cards:
                if platform_cards[key]["tenant"] != _PLATFORM_TENANT:
                    raise PersonaError(
                        f"platform library card {key[1]!r} must declare "
                        f"tenant: {_PLATFORM_TENANT}"
                    )
            found.update(platform_cards)
        return found

    def card_file(self, tenant: str, persona: str) -> Path:
        return self.cards_dir / f"{persona}.yaml"

    # -- read ------------------------------------------------------------- #
    def get(
        self, tenant: str, persona: str, fallback_to_platform: bool = True
    ) -> Dict[str, Any]:
        """Resolve a persona card, tenant-first with a platform fallback.

        A tenant card shadows the platform card of the same id. When no
        tenant card exists and ``fallback_to_platform`` is set, the platform
        default is returned (tenant-extensible library). There is never a
        fallback across other tenants.
        """
        cards = self.discover()
        key = _key(tenant, persona)
        if key in cards:
            return cards[key]
        if fallback_to_platform and tenant != _PLATFORM_TENANT:
            pkey = _key(_PLATFORM_TENANT, persona)
            if pkey in cards:
                return cards[pkey]
        raise UnknownPersonaError(f"unknown persona {persona!r} for tenant {tenant!r}")

    def ledger(self) -> List[Dict[str, Any]]:
        return _read_manifest(self.manifest_path)["entries"]

    def _last_record(self, tenant: str, persona: str) -> Optional[Dict[str, Any]]:
        recs = [
            r
            for r in self.ledger()
            if r.get("tenant") == tenant and r.get("persona") == persona
        ]
        return recs[-1] if recs else None

    def lifecycle_status(self, tenant: str, persona: str) -> Optional[str]:
        rec = self._last_record(tenant, persona)
        return rec.get("status") if rec else None

    # -- lifecycle -------------------------------------------------------- #
    def publish(self, tenant: str, persona: str) -> Dict[str, Any]:
        """Freeze the current card version in the ledger as published.

        - unknown persona                                -> UnknownPersonaError
        - already published with the same digest         -> no-op (idempotent)
        - published (any earlier record) with a different digest for this
          version                                       -> AlreadyPublishedError
          (a published version is immutable: bump the card version and re-publish)
        - last record retired for this exact version     -> AlreadyPublishedError
          (a retired version stays retired; publish a new version)
        """
        card = self.get(tenant, persona, fallback_to_platform=False)
        fpath = self.card_file(tenant, persona)
        digest = _sha256(fpath)
        version = card["version"]

        last = self._last_record(tenant, persona)
        if last and last["version"] == version and last["sha256"] == digest:
            if last["status"] == "published":
                return last  # idempotent re-publish of the same snapshot
            raise AlreadyPublishedError(
                f"persona {tenant}/{persona} version {version} is retired; "
                "bump the card version to reactivate it"
            )
        if last and last["version"] == version and last["sha256"] != digest:
            raise AlreadyPublishedError(
                f"persona {tenant}/{persona} version {version} is already "
                "published under a different digest; bump the card version "
                "and re-publish (published versions are immutable)"
            )

        record = {
            "tenant": tenant,
            "persona": persona,
            "version": version,
            "file": _record_file(fpath),
            "sha256": digest,
            "status": "published",
        }
        manifest = _read_manifest(self.manifest_path)
        manifest["entries"].append(record)
        _write_manifest(manifest, self.manifest_path)
        return record

    def publish_all(self) -> List[Dict[str, Any]]:
        """Publish every discovered persona in deterministic (tenant, id) order."""
        cards = self.discover()
        records = []
        for (tenant, persona) in sorted(cards):
            records.append(self.publish(tenant, persona))
        return records

    def retire(self, tenant: str, persona: str) -> Dict[str, Any]:
        """Retire a published persona (append-only). Retired personas no longer resolve."""
        card = self.get(tenant, persona, fallback_to_platform=False)
        fpath = self.card_file(tenant, persona)
        last = self._last_record(tenant, persona)
        if last and last["status"] == "retired":
            raise AlreadyPublishedError(f"persona {tenant}/{persona} is already retired")
        record = {
            "tenant": tenant,
            "persona": persona,
            "version": card["version"],
            "file": _record_file(fpath),
            "sha256": _sha256(fpath),
            "status": "retired",
        }
        manifest = _read_manifest(self.manifest_path)
        manifest["entries"].append(record)
        _write_manifest(manifest, self.manifest_path)
        return record

    def resolve(self, tenant: str, persona: str) -> Dict[str, Any]:
        """Resolve the dispatchable persona for (tenant, persona).

        Refuses unknown, unpublished, retired and integrity-violated personas
        (governance: no ad-hoc unversioned persona dispatch).
        """
        card = self.get(tenant, persona)
        last = self._last_record(card["tenant"], persona)
        if last is None:
            raise UnpublishedPersonaError(
                f"persona {card['tenant']}/{persona} is not published"
            )
        if last["status"] == "retired":
            raise RetiredPersonaError(
                f"persona {card['tenant']}/{persona} is retired"
            )
        fpath = self.card_file(card["tenant"], persona)
        if _sha256(fpath) != last["sha256"]:
            raise IntegrityError(
                f"persona {card['tenant']}/{persona} version {last['version']} "
                "was modified after publishing (sha mismatch); bump the card "
                "version and re-publish"
            )
        if card["version"] != last["version"]:
            raise IntegrityError(
                f"persona {card['tenant']}/{persona} on-disk version "
                f"{card['version']} differs from published {last['version']}"
            )
        return card


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_status(registry: PersonaRegistry) -> int:
    cards = registry.discover()
    catalog = registry.catalog
    summary = {
        "personas": len(cards),
        "tenants": sorted({t for (t, _p) in cards}),
        "postures": {},
        "ledger": len(registry.ledger()),
        "catalog": {
            "tools": len(catalog["tools"]),
            "capabilities": len(catalog["capabilities"]),
            "constraints": len(catalog["constraints"]),
            "guardrailPolicies": len(catalog["guardrailPolicies"]),
            "tiers": len(catalog["tiers"]),
            "memoryScopes": len(catalog["memoryScopes"]),
        },
    }
    for (_t, _p), card in cards.items():
        summary["postures"][card["posture"]] = (
            summary["postures"].get(card["posture"], 0) + 1
        )
    print(json.dumps(summary, indent=2, sort_keys=True))
    for (tenant, persona) in sorted(cards):
        status = registry.lifecycle_status(tenant, persona) or "draft"
        print(f"  {tenant}/{persona:<16} v{cards[(tenant, persona)]['version']:<6} {status}")
    return 0


def _cmd_validate(paths: List[str]) -> int:
    failed = 0
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            print(f"FAIL  {raw}: not found", file=sys.stderr)
            failed += 1
            continue
        try:
            card = card_from_yaml(path)
            print(f"OK    {raw} ({card['tenant']}/{card['id']} v{card['version']})")
        except PersonaError as exc:
            print(f"FAIL  {raw}: {exc}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def _require_persona(args: argparse.Namespace) -> Tuple[str, str]:
    if not _ID_RE.match(args.persona or ""):
        raise PersonaError(f"invalid persona id {args.persona!r}")
    return args.tenant or _PLATFORM_TENANT, args.persona


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="personas.registry")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="summarize discovered personas + ledger")
    p_validate = sub.add_parser("validate", help="validate one or more card files")
    p_validate.add_argument("paths", nargs="+")

    p_resolve = sub.add_parser("resolve", help="resolve a published persona")
    p_resolve.add_argument("persona")
    p_resolve.add_argument("--tenant", default=None)

    p_publish = sub.add_parser("publish", help="publish a persona version")
    p_publish.add_argument("persona")
    p_publish.add_argument("--tenant", default=None)

    p_retire = sub.add_parser("retire", help="retire a published persona")
    p_retire.add_argument("persona")
    p_retire.add_argument("--tenant", default=None)

    sub.add_parser("publish-all", help="publish every discovered persona (seed step)")

    args = parser.parse_args(argv)
    registry = PersonaRegistry()

    try:
        if args.command == "status":
            return _print_status(registry)
        if args.command == "validate":
            return _cmd_validate(args.paths)
        if args.command == "resolve":
            tenant, persona = _require_persona(args)
            card = registry.resolve(tenant, persona)
            print(
                f"OK {tenant}/{persona} v{card['version']} "
                f"(posture={card['posture']}, tier={card['defaultModelTier']})"
            )
            return 0
        if args.command == "publish":
            tenant, persona = _require_persona(args)
            rec = registry.publish(tenant, persona)
            print(f"published {rec['tenant']}/{rec['persona']} v{rec['version']}")
            return 0
        if args.command == "retire":
            tenant, persona = _require_persona(args)
            rec = registry.retire(tenant, persona)
            print(f"retired {rec['tenant']}/{rec['persona']} v{rec['version']}")
            return 0
        if args.command == "publish-all":
            records = registry.publish_all()
            print(f"publish-all: {len(records)} persona(s) published/verified")
            return 0
    except PersonaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
