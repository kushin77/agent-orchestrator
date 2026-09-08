"""Profile and category catalogs: validated YAML contract documents.

The sandbox contract is instantiated from two YAML documents validated
against the JSON Schema contract (``profiles.schema.json`` /
``categories.schema.json``): ``profiles.yaml`` declares the three security
profiles and ``categories.yaml`` binds each agent tool category to a profile
with a fail-closed default. Loading is strict: a document that does not
validate is a :class:`SandboxConfigError` - never a silent partial load and
never a silent fallback to a weaker profile. Unknown categories resolve to the
document's ``defaultProfile`` (restricted) - fail closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import yaml

from .errors import SandboxConfigError
from .model import PROFILE_NAMES, SecurityProfile
from .schema import load_schema, validate_document

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_PROFILES_PATH = os.path.join(_HERE, "profiles.yaml")
_DEFAULT_CATEGORIES_PATH = os.path.join(_HERE, "categories.yaml")


def read_yaml_document(path: str) -> Mapping[str, Any]:
    """Load a YAML contract document as a mapping (strict)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise SandboxConfigError(f"{path}: invalid YAML: {exc}") from exc
    except OSError as exc:
        raise SandboxConfigError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(doc, Mapping):
        raise SandboxConfigError(
            f"{path}: expected a mapping document, got {type(doc).__name__}"
        )
    return dict(doc)


@dataclass(frozen=True)
class ProfileCatalog:
    """The resolved profile set (``profiles.yaml`` instantiated + validated)."""

    default_profile: str
    profiles: Mapping[str, SecurityProfile] = field(repr=False)

    def __post_init__(self) -> None:
        if self.default_profile not in self.profiles:
            raise SandboxConfigError(
                f"default_profile {self.default_profile!r} is not a declared "
                f"profile (declared: {sorted(self.profiles)})"
            )

    def profile(self, name: str) -> SecurityProfile:
        """The profile named ``name``; raises ``KeyError`` when undeclared."""
        try:
            return self.profiles[name]
        except KeyError:
            raise KeyError(
                f"unknown security profile {name!r} "
                f"(declared: {sorted(self.profiles)})"
            ) from None

    def default_security_profile(self) -> SecurityProfile:
        """The fail-closed profile used when nothing more specific applies."""
        return self.profiles[self.default_profile]


@dataclass(frozen=True)
class CategoryMap:
    """The tool category -> profile map (``categories.yaml`` instantiated)."""

    default_profile: str
    categories: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.default_profile not in PROFILE_NAMES:
            raise SandboxConfigError(
                f"default_profile {self.default_profile!r} is not one of "
                f"{PROFILE_NAMES}"
            )
        bad = sorted(
            cat for cat, profile in self.categories.items()
            if profile not in PROFILE_NAMES
        )
        if bad:
            raise SandboxConfigError(
                f"category map references undeclared profile(s): {bad}"
            )

    def profile_for(self, category: str) -> str:
        """Profile name for ``category`` - unknown categories fail closed to
        the map's ``default_profile`` (restricted)."""
        return self.categories.get(category, self.default_profile)

    def is_declared(self, category: str) -> bool:
        return category in self.categories


# --------------------------------------------------------------------------- #
# builders (validate the raw document, then type it - never a silent partial)
# --------------------------------------------------------------------------- #
def _build_catalog(doc: Mapping[str, Any]) -> ProfileCatalog:
    errors = validate_document(doc, load_schema("profiles"))
    if errors:
        raise SandboxConfigError("profiles document invalid: " + "; ".join(errors))
    raw_profiles = doc["profiles"]
    profiles = {
        name: SecurityProfile.from_doc(name, raw)
        for name, raw in raw_profiles.items()
    }
    return ProfileCatalog(default_profile=str(doc["defaultProfile"]), profiles=profiles)


def _build_category_map(doc: Mapping[str, Any]) -> CategoryMap:
    errors = validate_document(doc, load_schema("categories"))
    if errors:
        raise SandboxConfigError("categories document invalid: " + "; ".join(errors))
    return CategoryMap(
        default_profile=str(doc["defaultProfile"]),
        categories={str(k): str(v) for k, v in doc["categories"].items()},
    )


def load_profile_catalog(path: Optional[str] = None) -> ProfileCatalog:
    """Load + validate a profiles document; defaults to the packaged one."""
    return _build_catalog(read_yaml_document(path or _DEFAULT_PROFILES_PATH))


def load_category_map(path: Optional[str] = None) -> CategoryMap:
    """Load + validate a categories document; defaults to the packaged one."""
    return _build_category_map(read_yaml_document(path or _DEFAULT_CATEGORIES_PATH))


# --------------------------------------------------------------------------- #
# packaged defaults (module-level; immutable, so safe to share)
# --------------------------------------------------------------------------- #
_DEFAULT_CATALOG: Optional[ProfileCatalog] = None
_DEFAULT_CATEGORY_MAP: Optional[CategoryMap] = None


def default_profile_catalog() -> ProfileCatalog:
    """The packaged security profiles (restricted/standard/privileged)."""
    global _DEFAULT_CATALOG
    if _DEFAULT_CATALOG is None:
        _DEFAULT_CATALOG = load_profile_catalog()
    return _DEFAULT_CATALOG


def default_category_map() -> CategoryMap:
    """The packaged category -> profile map (fail-closed default restricted)."""
    global _DEFAULT_CATEGORY_MAP
    if _DEFAULT_CATEGORY_MAP is None:
        _DEFAULT_CATEGORY_MAP = load_category_map()
    return _DEFAULT_CATEGORY_MAP


def resolve_profile_for_category(
    category: str,
    category_map: Optional[CategoryMap] = None,
    catalog: Optional[ProfileCatalog] = None,
) -> SecurityProfile:
    """Resolve the security profile for a tool category (fail closed).

    Unknown categories resolve to the map's default profile; if the resolved
    profile name is somehow not in the catalog the default profile is used
    instead - both steps fail toward ``restricted``, never toward a weaker
    profile.
    """
    category_map = category_map or default_category_map()
    catalog = catalog or default_profile_catalog()
    name = category_map.profile_for(category)
    try:
        return catalog.profile(name)
    except KeyError:
        return catalog.default_security_profile()


def resolve_profile(
    name: str,
    catalog: Optional[ProfileCatalog] = None,
) -> SecurityProfile:
    """Resolve a profile by name, failing closed to the default profile.

    An unknown explicit name (e.g. a tool that asks for a profile that does
    not exist) must never widen the sandbox - it resolves to ``restricted``,
    the most secure profile.
    """
    catalog = catalog or default_profile_catalog()
    try:
        return catalog.profile(name)
    except KeyError:
        return catalog.default_security_profile()
