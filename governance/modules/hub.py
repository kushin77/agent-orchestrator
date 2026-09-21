"""The hub side of the registry — read the authority, mirror its own gate.

---knowledge---
module_id: governance.modules.hub
system: governance
app: modules
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Seed, HubModule, MandatoryRow, HubCatalog, safe_asset_path, seed_path, read_mandatory_tsv, load]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

Authority: the pinned hub submodule ``vendor/CMR`` (read-only). Two surfaces
are read and **must agree**:

* the **flags** — ``catalog/modules/<id>/module.json`` carrying ``mandatory``
  and ``mandatory_consumer_assets``;
* the **registry** — ``catalog/mandatory.tsv``
  (``id | module | consumer_assets | reason``).

Drift between them in *either* direction is a refusal, and every mandatory
consumer asset must resolve to a shipped seed under ``templates/module/`` or
``guardrails/``. That is a deliberate mirror of the hub's own
``catalog/validate.py`` (GR-18) — the hub's gate and this repo's reading of the
hub cannot drift apart silently.

A missing or malformed hub catalog raises :class:`CannotAssess`: a clean clone
has no submodule, and the honest answer there is *cannot assess*, never a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from governance.modules.model import (
    CannotAssess,
    Refusal,
    sorted_refusals,
)

#: Default hub root, relative to the repository root.
DEFAULT_HUB = "vendor/CMR"

CATALOG = "catalog"
MODULES = "modules"
MANDATORY_TSV = "mandatory.tsv"
MANDATORY_HEADER = ["id", "module", "consumer_assets", "reason"]
TEMPLATE_MODULE = "templates/module"
GUARDRAILS = "guardrails"

REVISION_FROM_CHECKOUT = "hub-checkout"
REVISION_FROM_GITLINK = "submodule-gitlink"
REVISION_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Seed:
    """A consumer asset resolved to the seed the hub ships for it."""

    asset: str
    path: Optional[str]
    present: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"asset": self.asset, "path": self.path, "present": self.present}


@dataclass(frozen=True)
class HubModule:
    """One ``catalog/modules/<dir>/module.json`` entry, read not interpreted."""

    id: str
    dir_name: str
    name: str
    mandatory: bool
    consumer_assets: Tuple[str, ...]
    repo: Optional[str]
    pin: Optional[str]
    board_ref: Optional[str]
    package: Optional[str]
    language: Optional[str]
    manifest_path: str
    seeds: Tuple[Seed, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dir_name": self.dir_name,
            "name": self.name,
            "mandatory": self.mandatory,
            "consumer_assets": list(self.consumer_assets),
            "repo": self.repo,
            "pin": self.pin,
            "board_ref": self.board_ref,
            "package": self.package,
            "language": self.language,
            "manifest": self.manifest_path,
            "seeds": [seed.as_dict() for seed in self.seeds],
        }


@dataclass(frozen=True)
class MandatoryRow:
    """One ``catalog/mandatory.tsv`` row."""

    id: str
    module: str
    assets: Tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class HubCatalog:
    """The hub catalog as read, with every drift/seed finding it produced.

    ``root`` is the hub root **as recorded** (``vendor/CMR`` for the pinned
    submodule, so the document stays portable); ``resolved_root`` is the
    absolute path the files were actually read from.
    """

    root: str
    resolved_root: str
    revision: Optional[str]
    revision_source: str
    modules: Tuple[HubModule, ...]
    mandatory_rows: Tuple[MandatoryRow, ...]
    refusals: Tuple[Refusal, ...]

    @property
    def ids(self) -> frozenset:
        return frozenset(module.id for module in self.modules)

    def by_id(self, module_id: str) -> Optional[HubModule]:
        for module in self.modules:
            if module.id == module_id:
                return module
        return None

    @property
    def mandatory_ids(self) -> frozenset:
        return frozenset(module.id for module in self.modules if module.mandatory)

    def reference_path(self, module_id: str) -> Optional[str]:
        """Hub-relative path of a module's catalog entry (a reference, never a copy)."""
        module = self.by_id(module_id)
        return module.manifest_path if module else None

    def entry_path(self, module_id: str) -> str:
        """Hub-relative directory a module's catalog entry would live in."""
        module = self.by_id(module_id)
        if module is None:
            return "{}/{}/{}".format(CATALOG, MODULES, module_id)
        return "{}/{}/{}".format(CATALOG, MODULES, module.dir_name)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _git(args: List[str], cwd: Path) -> Optional[str]:
    """Best-effort git probe; ``None`` when git is missing or the call fails."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _revision(hub_root: Path, repo_root: Path) -> Tuple[Optional[str], str]:
    """The pin this registry was built from — a checkout sha, else the gitlink."""
    if _git(["rev-parse", "--is-inside-work-tree"], hub_root) == "true":
        sha = _git(["rev-parse", "HEAD"], hub_root)
        if sha:
            return sha, REVISION_FROM_CHECKOUT
    try:
        is_pinned = hub_root.resolve() == (repo_root / DEFAULT_HUB).resolve()
    except OSError:
        is_pinned = False
    if is_pinned:
        link = _git(["ls-files", "-s", DEFAULT_HUB], repo_root)
        if link:
            parts = link.split()
            if len(parts) >= 2 and parts[0] == "160000":
                return parts[1], REVISION_FROM_GITLINK
    return None, REVISION_UNAVAILABLE


def _rel(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(str(path), str(base))
    except ValueError:  # pragma: no cover - different drives (Windows only)
        return str(path)


def safe_asset_path(asset: Any) -> Optional[str]:
    """Reject anything but a clean repo-relative consumer-asset path.

    Mirrors the hub's ``catalog/validate.py::safe_asset_path`` so an asset the
    hub would refuse can never be resolved here either.
    """
    if not isinstance(asset, str) or not asset.strip():
        return None
    if asset.startswith(("/", "\\")) or "\\" in asset:
        return None
    if any(part in ("", ".", "..") for part in asset.split("/")):
        return None
    return asset


def seed_path(hub_root: Path, asset: str) -> Optional[Path]:
    """Resolve an asset to its shipped seed (``templates/module/`` or ``guardrails/``)."""
    for root in (hub_root / TEMPLATE_MODULE, hub_root / GUARDRAILS):
        candidate = root / asset
        if candidate.is_file():
            return candidate
    return None


def read_mandatory_tsv(path: Path) -> Tuple[Dict[str, MandatoryRow], Optional[str]]:
    """Parse the mandatory registry, mirroring the hub's ``load_mandatory_tsv``."""
    if not path.is_file():
        return {}, "registry not found: {}".format(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {}, "cannot read {}: {}".format(path, exc)
    rows: Dict[str, MandatoryRow] = {}
    header = False
    for lineno, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = [col.strip() for col in line.split("\t")]
        if not header:
            if cols != MANDATORY_HEADER:
                return {}, "{}:{:d}: bad header {!r} (expected {!r})".format(
                    path, lineno, line, "\t".join(MANDATORY_HEADER)
                )
            header = True
            continue
        if len(cols) != 4 or not all(cols):
            return {}, "{}:{:d}: malformed row (expected 4 non-empty columns)".format(
                path, lineno
            )
        module_id, name, assets, reason = cols
        if module_id in rows:
            return {}, "{}:{:d}: duplicate id {!r}".format(path, lineno, module_id)
        rows[module_id] = MandatoryRow(
            id=module_id,
            module=name,
            assets=tuple(a for a in (p.strip() for p in assets.split(",")) if a),
            reason=reason,
        )
    if not header:
        return {}, "{}: no header row found".format(path)
    return rows, None


def _module(
    module_id: str,
    dir_name: str,
    data: Dict[str, Any],
    manifest: Path,
    hub_root: Path,
    refusals: List[Refusal],
) -> HubModule:
    rel = _rel(manifest, hub_root)

    flag = data.get("mandatory", False)
    if flag is None:
        flag = False
    if not isinstance(flag, bool):
        refusals.append(
            Refusal(
                "MODULE-MANDATORY-FLAG-INVALID",
                module_id,
                "`mandatory` must be a boolean (got {!r})".format(flag),
                rel,
            )
        )
        flag = False

    declared = data.get("mandatory_consumer_assets")
    assets: List[str] = []
    if declared is None:
        declared = []
    if not isinstance(declared, list):
        refusals.append(
            Refusal(
                "MODULE-ASSET-NO-LIST",
                module_id,
                "`mandatory_consumer_assets` must be a list (got {!r})".format(declared),
                rel,
            )
        )
        declared = []
    if flag and not declared:
        refusals.append(
            Refusal(
                "MODULE-ASSET-NO-LIST",
                module_id,
                "a mandatory module must declare a non-empty "
                "`mandatory_consumer_assets` list",
                rel,
            )
        )
    if not flag and data.get("mandatory_consumer_assets") is not None:
        refusals.append(
            Refusal(
                "MODULE-NONMANDATORY-DECLARES-ASSETS",
                module_id,
                "a non-mandatory module must not declare `mandatory_consumer_assets`",
                rel,
            )
        )

    seeds: List[Seed] = []
    for asset in declared:
        safe = safe_asset_path(asset)
        if safe is None:
            refusals.append(
                Refusal(
                    "MODULE-ASSET-UNSAFE",
                    module_id,
                    "invalid consumer asset path {!r} (relative path, no `..`)".format(asset),
                    rel,
                )
            )
            continue
        assets.append(safe)
        if not flag:
            continue
        found = seed_path(hub_root, safe)
        if found is None:
            refusals.append(
                Refusal(
                    "MODULE-ASSET-NO-SEED",
                    module_id,
                    "mandatory consumer asset {!r} has no seed under "
                    "templates/module/ or guardrails/".format(safe),
                    rel,
                )
            )
            seeds.append(Seed(safe, None, False))
            continue
        seeds.append(Seed(safe, _rel(found, hub_root), True))

    source = data.get("source") or {}
    distribution = data.get("distribution") or {}
    versions = data.get("versions") or {}
    governance = data.get("governance") or {}
    package = distribution.get("package")
    return HubModule(
        id=module_id,
        dir_name=dir_name,
        name=str(data.get("name") or module_id),
        mandatory=flag,
        consumer_assets=tuple(sorted(assets)),
        repo=source.get("repo"),
        pin=versions.get("latest"),
        board_ref=governance.get("direction_issue"),
        package=package if package else None,
        language=distribution.get("language"),
        manifest_path=rel,
        seeds=tuple(sorted(seeds, key=lambda s: s.asset)),
    )


def _read_modules(
    hub_root: Path, catalog_root: Path
) -> Tuple[List[HubModule], List[Refusal]]:
    modules: List[HubModule] = []
    refusals: List[Refusal] = []
    modules_dir = catalog_root / MODULES
    if not modules_dir.is_dir():
        raise CannotAssess("hub modules directory not found: {}".format(modules_dir))

    seen: Dict[str, str] = {}
    for entry in sorted(p for p in modules_dir.iterdir() if p.is_dir()):
        manifest = entry / "module.json"
        if not manifest.is_file():
            continue
        rel = _rel(manifest, hub_root)
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            refusals.append(
                Refusal(
                    "MODULE-MANIFEST-INVALID",
                    entry.name,
                    "unparseable manifest: {}".format(exc),
                    rel,
                )
            )
            continue
        if not isinstance(data, dict) or not data.get("id"):
            refusals.append(
                Refusal(
                    "MODULE-MANIFEST-INVALID",
                    entry.name,
                    "manifest carries no id",
                    rel,
                )
            )
            continue
        module_id = str(data["id"])
        if module_id != entry.name:
            refusals.append(
                Refusal(
                    "MODULE-MANIFEST-INVALID",
                    module_id,
                    "id does not equal its directory name {!r}".format(entry.name),
                    rel,
                )
            )
        if module_id in seen:
            refusals.append(
                Refusal(
                    "MODULE-DUPLICATE-ID",
                    module_id,
                    "declared twice: {} and {}".format(seen[module_id], rel),
                    rel,
                )
            )
            continue
        seen[module_id] = rel
        modules.append(_module(module_id, entry.name, data, manifest, hub_root, refusals))
    return modules, refusals


def _drift(
    modules: List[HubModule], rows: Dict[str, MandatoryRow], refusals: List[Refusal]
) -> None:
    """The two surfaces in lockstep — drift in either direction is refused."""
    mandatory = [module for module in modules if module.mandatory]
    for module in mandatory:
        row = rows.get(module.id)
        if row is None:
            refusals.append(
                Refusal(
                    "MODULE-UNREGISTERED-FLAG",
                    module.id,
                    "flagged `mandatory: true` but the mandatory registry has no row "
                    "for it — unregistered in the registry, registered in the flags",
                    module.manifest_path,
                )
            )
            continue
        if row.module != module.name:
            refusals.append(
                Refusal(
                    "MODULE-NAME-DRIFT",
                    module.id,
                    "registry module column {!r} != manifest name {!r}".format(
                        row.module, module.name
                    ),
                    MANDATORY_TSV,
                )
            )
        if sorted(row.assets) != sorted(module.consumer_assets):
            refusals.append(
                Refusal(
                    "MODULE-ASSET-DRIFT",
                    module.id,
                    "registry consumer_assets {!r} != manifest {!r}".format(
                        list(row.assets), list(module.consumer_assets)
                    ),
                    MANDATORY_TSV,
                )
            )
    for extra in sorted(set(rows) - {module.id for module in mandatory}):
        refusals.append(
            Refusal(
                "MODULE-UNREGISTERED-MANDATORY",
                extra,
                "the mandatory registry names it as mandatory, but the catalog "
                "registers no module under that id with `mandatory: true`",
                MANDATORY_TSV,
            )
        )


def load(hub_root: Path, repo_root: Path, recorded_root: Optional[str] = None) -> HubCatalog:
    """Read the hub catalog. Raises :class:`CannotAssess` when it cannot be read.

    A relative ``hub_root`` is resolved against ``repo_root`` and recorded as
    given, so the default build records ``vendor/CMR`` rather than an absolute
    path that would differ per checkout.
    """
    hub_root = Path(hub_root)
    repo_root = Path(repo_root)
    if not hub_root.is_absolute():
        hub_root = repo_root / hub_root
    root = str(recorded_root) if recorded_root else str(hub_root)
    catalog_root = hub_root / CATALOG
    tsv_path = catalog_root / MANDATORY_TSV
    if not catalog_root.is_dir():
        raise CannotAssess("hub catalog not found: {}".format(catalog_root))
    if not tsv_path.is_file():
        raise CannotAssess("hub mandatory registry not found: {}".format(tsv_path))

    modules, refusals = _read_modules(hub_root, catalog_root)
    rows, err = read_mandatory_tsv(tsv_path)
    if err:
        raise CannotAssess("hub mandatory registry unreadable: {}".format(err))
    _drift(modules, rows, refusals)

    revision, revision_source = _revision(hub_root, repo_root)
    return HubCatalog(
        root=root,
        resolved_root=str(hub_root),
        revision=revision,
        revision_source=revision_source,
        modules=tuple(sorted(modules, key=lambda m: m.id)),
        mandatory_rows=tuple(rows[key] for key in sorted(rows)),
        refusals=sorted_refusals(refusals),
    )
