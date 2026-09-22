"""No vendoring — the registry stores references, and the gate proves it.

---knowledge---
module_id: governance.modules.vendoring
system: governance
app: modules
solution_class: pattern
patterns: [deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [check_references, scan]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

Acceptance 4: the registry stores **references** (id, repo, pin, path), and a
gate finding fires when a module's *source* is carried in-tree instead of
referenced. A vendored submodule is a reference, not a copy: ``vendor/CMR`` is
the pinned, read-only hub (the authority this package reads) and
``vendor/AgenticAutomationFramework`` is the pinned, read-only Playwright E2E
module. The permitted set is declared **once**, in ``VENDOR_SUBMODULES`` below —
never restated — so a doctrine move stays a one-line change here.

Four rules, each mechanically provokable in a scratch copy:

* ``VENDOR-PATH-OUTSIDE-HUB`` — every registry reference must live under the
  hub root; a "reference" pointing at in-tree module source is a copy;
* ``VENDOR-SOURCE-IN-TREE`` — no ``module.json`` outside ``vendor/`` may
  declare a hub-catalog module id, and no ``.gitmodules`` entry may vendor a
  module anywhere but through the hub;
* ``VENDOR-EXTRA-SUBMODULE`` — a ``.gitmodules`` path outside
  ``VENDOR_SUBMODULES`` is an undeclared vendor path;
* ``VENDOR-IN-TREE-PACKAGE`` — a module's declared distribution package must
  not exist in-tree (``codeidx/``, ``node_modules/@kushin77/saas-rbac``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from governance.modules.model import Refusal, sorted_refusals

#: Directories the in-tree scan never descends into. ``vendor`` is the hub
#: itself (a reference, by definition); ``node_modules`` is checked explicitly
#: by the package rule instead of being walked.
SKIP_DIRS = frozenset({".git", ".research", ".verify", "vendor", "node_modules"})

MANIFEST_NAME = "module.json"
GITMODULES = ".gitmodules"
HUB_SUBMODULE_PATH = "vendor/CMR"

#: The declared vendored submodules this repository permits — the single
#: authority the ``VENDOR-EXTRA-SUBMODULE`` arm reads, rather than a literal
#: restated in the rule. Every entry is a pinned, read-only reference (never an
#: in-tree copy): ``vendor/CMR`` is the governance hub and
#: ``vendor/AgenticAutomationFramework`` is the Playwright E2E module (#2012).
#: A path outside this set is refused by name, so vendoring a third submodule is
#: a red gate until it is declared here in the same commit as its ``.gitmodules``
#: entry.
VENDOR_SUBMODULES: Tuple[str, ...] = (
    HUB_SUBMODULE_PATH,
    "vendor/AgenticAutomationFramework",
)


def _manifest_ids(repo_root: Path, hub_ids: frozenset) -> List[Refusal]:
    """Rule: a hub module's manifest copied in-tree is in-tree source."""
    refusals: List[Refusal] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        if MANIFEST_NAME not in filenames:
            continue
        if Path(dirpath).resolve() == Path(repo_root).resolve():
            continue  # the repo's own root manifest is this repo, not a module copy
        manifest = Path(dirpath) / MANIFEST_NAME
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        module_id = data.get("id")
        if isinstance(module_id, str) and module_id in hub_ids:
            refusals.append(
                Refusal(
                    "VENDOR-SOURCE-IN-TREE",
                    module_id,
                    "a hub module manifest is carried in-tree; store a reference "
                    "(id, repo, pin, path) instead",
                    _rel(manifest, repo_root),
                )
            )
    return refusals


def _gitmodules(repo_root: Path, hub_ids: frozenset) -> List[Refusal]:
    """Rules: only declared vendor paths, and none of them names a module."""
    path = Path(repo_root) / GITMODULES
    if not path.is_file():
        return []
    refusals: List[Refusal] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("path"):
            continue
        _, _, value = stripped.partition("=")
        submodule_path = value.strip().strip('"')
        if not submodule_path or submodule_path in VENDOR_SUBMODULES:
            continue
        if submodule_path in hub_ids or os.path.basename(submodule_path) in hub_ids:
            refusals.append(
                Refusal(
                    "VENDOR-SOURCE-IN-TREE",
                    os.path.basename(submodule_path),
                    "submodule {} vendors a hub module directly; a hub module is "
                    "referenced through the hub ({}), never vendored".format(
                        submodule_path, HUB_SUBMODULE_PATH
                    ),
                    GITMODULES,
                )
            )
            continue
        refusals.append(
            Refusal(
                "VENDOR-EXTRA-SUBMODULE",
                submodule_path,
                "the declared vendored submodules are {}; {} is an undeclared "
                "vendor path".format(", ".join(VENDOR_SUBMODULES), submodule_path),
                GITMODULES,
            )
        )
    return refusals


def _package_dirs(repo_root: Path, modules: Iterable[Any]) -> List[Refusal]:
    """Rule: a module's distribution package must not exist in this tree."""
    refusals: List[Refusal] = []
    for module in modules:
        package = getattr(module, "package", None)
        if not package:
            continue
        for candidate in (Path(repo_root) / package, Path(repo_root) / "node_modules" / package):
            if candidate.is_dir():
                refusals.append(
                    Refusal(
                        "VENDOR-IN-TREE-PACKAGE",
                        package,
                        "module {!r} is distributed as this package and it exists "
                        "in-tree at {}; reference the pin instead".format(
                            getattr(module, "id", "?"), _rel(candidate, repo_root)
                        ),
                        _rel(candidate, repo_root),
                    )
                )
    return refusals


def check_references(hub_root: Path, entries: Iterable[Dict[str, Any]]) -> List[Refusal]:
    """Rule: every hub reference must resolve under the hub root, not in-tree."""
    refusals: List[Refusal] = []
    hub = Path(hub_root)
    for entry in entries:
        reference = entry.get("reference") or {}
        if reference.get("kind") != "hub-catalog-entry":
            continue
        recorded = reference.get("path")
        if not recorded:
            continue
        resolved = (hub / recorded).resolve()
        try:
            inside = resolved.is_relative_to(hub.resolve())
        except (OSError, ValueError):  # pragma: no cover - defensive
            inside = False
        if not inside:
            refusals.append(
                Refusal(
                    "VENDOR-PATH-OUTSIDE-HUB",
                    str(entry.get("id", "?")),
                    "registry reference {!r} resolves outside the hub root; a "
                    "reference may never point at in-tree source".format(recorded),
                    str(hub_root),
                )
            )
    return refusals


def scan(
    repo_root: Path,
    catalog: Any,
    entries: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Refusal]:
    """Every in-tree vendoring finding, deterministic and sorted."""
    hub_ids = frozenset(catalog.ids)
    refusals: List[Refusal] = []
    refusals.extend(_manifest_ids(Path(repo_root), hub_ids))
    refusals.extend(_gitmodules(Path(repo_root), hub_ids))
    refusals.extend(_package_dirs(Path(repo_root), catalog.modules))
    if entries is not None:
        refusals.extend(check_references(Path(catalog.resolved_root), entries))
    return list(sorted_refusals(refusals))


def _rel(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(str(path), str(base))
    except ValueError:  # pragma: no cover - different drives (Windows only)
        return str(path)
