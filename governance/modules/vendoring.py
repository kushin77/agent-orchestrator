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
referenced. The only vendor path in this repository stays ``vendor/CMR`` — the
pinned, read-only hub submodule.

Four rules, each mechanically provokable in a scratch copy:

* ``VENDOR-PATH-OUTSIDE-HUB`` — every registry reference must live under the
  hub root; a "reference" pointing at in-tree module source is a copy;
* ``VENDOR-SOURCE-IN-TREE`` — no ``module.json`` outside ``vendor/`` may
  declare a hub-catalog module id, and no ``.gitmodules`` entry may vendor a
  module anywhere but through the hub;
 * ``VENDOR-EXTRA-SUBMODULE`` — every vendored submodule must be SHA-pinned: an
   extra submodule carrying a ``branch =`` key (which ``git submodule update
   --remote`` walks to that branch's tip, destroying the pin) is refused, as is a
   ``path`` line outside any ``[submodule "..."]`` section;
* ``VENDOR-IN-TREE-PACKAGE`` — a module's declared distribution package must
  not exist in-tree (``codeidx/``, ``node_modules/@kushin77/saas-rbac``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from governance.modules.model import Refusal, sorted_refusals

#: Directories the in-tree scan never descends into. ``vendor`` is the hub
#: itself (a reference, by definition); ``node_modules`` is checked explicitly
#: by the package rule instead of being walked.
SKIP_DIRS = frozenset({".git", ".research", ".verify", "vendor", "node_modules"})

MANIFEST_NAME = "module.json"
GITMODULES = ".gitmodules"
HUB_SUBMODULE_PATH = "vendor/CMR"


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
    """Rules: every vendored submodule must be SHA-pinned, and name no module.

    The hub is the only submodule that may be vendored *for a module*. A second
    submodule is admitted when it is **pinned** — no ``branch`` key — because that
    is the property the vendoring contract actually depends on: a section carrying
    ``branch =`` lets ``git submodule update --remote`` walk the submodule to that
    branch's tip and destroys the pin, which is the hazard this repo's own
    ``.gitmodules`` header comment documents. So an *unpinned* extra submodule is
    refused by name, and a submodule that vendors a hub module stays refused as
    ``VENDOR-SOURCE-IN-TREE``.

    The file is parsed **by section** so a ``branch`` key is attributable to the
    submodule it belongs to; the flat line scan this replaced could not see one.
    """
    path = Path(repo_root) / GITMODULES
    if not path.is_file():
        return []
    refusals: List[Refusal] = []
    in_section = False
    submodule_path: Optional[str] = None
    branch_tracked = False

    def flush() -> None:
        """Judge one completed ``[submodule "..."]`` section."""
        if submodule_path is None or submodule_path == HUB_SUBMODULE_PATH:
            return
        if submodule_path in hub_ids or os.path.basename(submodule_path) in hub_ids:
            refusals.append(
                Refusal(
                    "VENDOR-SOURCE-IN-TREE",
                    os.path.basename(submodule_path),
                    "submodule {} vendors a hub module directly; the only vendor "
                    "path is {}".format(submodule_path, HUB_SUBMODULE_PATH),
                    GITMODULES,
                )
            )
            return
        if branch_tracked:
            refusals.append(
                Refusal(
                    "VENDOR-EXTRA-SUBMODULE",
                    submodule_path,
                    "submodule {} carries a `branch =` key, so `git submodule "
                    "update --remote` walks it to that branch's tip and destroys "
                    "the pin; every vendored submodule must be SHA-pinned like "
                    "{}".format(submodule_path, HUB_SUBMODULE_PATH),
                    GITMODULES,
                )
            )

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[submodule"):
            flush()
            in_section = True
            submodule_path = None
            branch_tracked = False
            continue
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key == "path":
            candidate = value.strip().strip('"')
            if not in_section:
                # A `path` outside any section is not a submodule declaration at
                # all: git ignores it, so it can never be the reference a vendored
                # submodule is supposed to be. Strictly worse than the old scan,
                # which judged such a line as if it were one.
                refusals.append(
                    Refusal(
                        "VENDOR-EXTRA-SUBMODULE",
                        candidate or "(empty)",
                        "a `path =` outside any `[submodule \"...\"]` section is not "
                        "a submodule declaration; git ignores it, so it cannot be a "
                        "vendoring reference",
                        GITMODULES,
                    )
                )
                continue
            submodule_path = candidate or None
        elif key == "branch":
            branch_tracked = True
    flush()
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
