"""Platform registry seed resolution for tenant provisioning (issue #14).

The provisioning pipeline installs a tenant's starter seed packs by reading the
on-disk platform registries (read-only). This module locates and fingerprints
the seed assets that the seed catalog (``seeds/``) references:

- profiles: ``registry/profiles/seeds/<id>.<version>.yaml``  (issue #9)
- prompts:  ``registry/prompts/modules/<taskType>.v<n>.yaml`` (issue #13)
- personas: ``registry/personas/cards/<id>.yaml``             (issue #11)

Issue #11's persona registry (registry/personas/cards) IS on master and cards
are installed as starter personas. For robustness on a checkout where that
directory is absent, persona references are recorded as *deferred* seeds: the
controller knows the contract ids and the expected directory path, records them
against the tenant, and installs them automatically once the registry is
present. When the persona directory IS present, resolution is fail-closed - an
expected persona id that cannot be found is an error, never a silent skip.

No network; everything resolves against the local checkout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from identity.onboarding.model import (
    SEED_KIND_PERSONA,
    SEED_KIND_PROFILE,
    SEED_KIND_PROMPT,
)


@dataclass(frozen=True)
class RegistrySeed:
    """The resolution of one seed reference against the platform registry."""

    kind: str
    ref: str
    version: str = ""
    source: str = ""  # registry-relative path when present
    present: bool = False
    deferrable: bool = False  # record as deferred rather than fail (issue #11)
    detail: str = ""


@dataclass(frozen=True)
class PersonaRegistryStatus:
    """Whether the persona-card registry (issue #11) is on this checkout."""

    dir_present: bool
    dir_path: str = ""
    catalog: dict[str, str] = field(default_factory=dict)  # id -> source
    versions: dict[str, str] = field(default_factory=dict)  # id -> card version


def default_repo_root() -> Path:
    """The git repo root, derived from this module's location.

    ``identity/onboarding/registry_assets.py`` -> parents[0]=onboarding,
    parents[1]=identity, parents[2]=repo root.
    """
    return Path(__file__).resolve().parents[2]


def profile_catalog(repo_root: Path | None = None) -> dict[str, RegistrySeed]:
    """Catalog of every seed profile in registry/profiles/seeds, keyed by ref."""
    root = repo_root or default_repo_root()
    seeds_dir = root / "registry" / "profiles" / "seeds"
    catalog: dict[str, RegistrySeed] = {}
    if not seeds_dir.is_dir():
        return catalog
    for path in sorted(seeds_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        seed_id = data.get("id")
        version = data.get("version")
        if not isinstance(seed_id, str) or not isinstance(version, str):
            continue
        ref = f"{seed_id}@{version}"
        catalog[ref] = RegistrySeed(
            kind=SEED_KIND_PROFILE,
            ref=ref,
            version=version,
            source=str(path.relative_to(root)),
            present=True,
        )
    return catalog


def prompt_catalog(repo_root: Path | None = None) -> dict[str, RegistrySeed]:
    """Catalog of every prompt module in registry/prompts/modules, keyed by ref."""
    root = repo_root or default_repo_root()
    modules_dir = root / "registry" / "prompts" / "modules"
    catalog: dict[str, RegistrySeed] = {}
    if not modules_dir.is_dir():
        return catalog
    for path in sorted(modules_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        task_type = data.get("taskType")
        version = data.get("version")
        if not isinstance(task_type, str) or not isinstance(version, str):
            continue
        if not version.startswith("v"):
            continue
        ref = f"{task_type}@{version}"
        catalog[ref] = RegistrySeed(
            kind=SEED_KIND_PROMPT,
            ref=ref,
            version=version,
            source=str(path.relative_to(root)),
            present=True,
        )
    return catalog


def persona_registry(repo_root: Path | None = None) -> PersonaRegistryStatus:
    """Status of the persona-card registry (issue #11) on this checkout.

    The cards are expected under ``registry/personas/cards/`` (file-per-persona,
    per issue #11's acceptance criteria). When the directory is absent, persona
    seeds are deferred rather than failed.
    """
    root = repo_root or default_repo_root()
    cards_dir = root / "registry" / "personas" / "cards"
    catalog: dict[str, str] = {}
    versions: dict[str, str] = {}
    if cards_dir.is_dir():
        for path in sorted(cards_dir.glob("*.yaml")):
            persona_id = None
            version = ""
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    candidate = data.get("id")
                    if isinstance(candidate, str) and candidate:
                        persona_id = candidate
                    card_version = data.get("version")
                    if isinstance(card_version, str) and card_version:
                        version = card_version
            except yaml.YAMLError:
                pass
            persona_id = persona_id or path.stem
            catalog[persona_id] = str(path.relative_to(root))
            versions[persona_id] = version
    return PersonaRegistryStatus(
        dir_present=cards_dir.is_dir(),
        dir_path=str(cards_dir.relative_to(root)) if cards_dir.exists() else "",
        catalog=catalog,
        versions=versions,
    )


def resolve(kind: str, ref: str, repo_root: Path | None = None) -> RegistrySeed:
    """Resolve one seed reference of ``kind`` against the local registry.

    Fail-closed: an unresolvable profile/prompt seed returns present=False
    (the caller decides whether that aborts provisioning). Persona seeds return
    present=False with the issue-#11 deferral note when the registry is absent.
    """
    root = repo_root or default_repo_root()
    if kind == SEED_KIND_PROFILE:
        return profile_catalog(root).get(ref, RegistrySeed(kind=kind, ref=ref))
    if kind == SEED_KIND_PROMPT:
        return prompt_catalog(root).get(ref, RegistrySeed(kind=kind, ref=ref))
    if kind == SEED_KIND_PERSONA:
        status = persona_registry(root)
        if not status.dir_present:
            return RegistrySeed(
                kind=kind,
                ref=ref,
                present=False,
                deferrable=True,
                detail=(
                    f"persona registry ({status.dir_path or 'registry/personas/cards'}) "
                    "not on this checkout (issue #11); persona seed deferred by contract"
                ),
            )
        source = status.catalog.get(ref)
        if source is None:
            return RegistrySeed(
                kind=kind,
                ref=ref,
                present=False,
                detail=f"persona id {ref!r} not found in persona registry",
            )
        return RegistrySeed(
            kind=kind,
            ref=ref,
            version=status.versions.get(ref, ""),
            present=True,
            source=source,
        )
    return RegistrySeed(kind=kind, ref=ref, present=False, detail=f"unknown seed kind {kind!r}")
