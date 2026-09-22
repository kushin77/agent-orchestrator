"""Shared fixtures for the module-brief suite (issue #447).

Every provocation runs against a scratch copy of the tree, never against the
lane's own files: the composer reads the registry, the hub catalog and the
persona card, and those inputs are mutated to prove the refusals bite.

``sys.dont_write_bytecode`` is set and every ``__pycache__`` in the scratch tree
is removed before it is imported: a stale bytecode file can shadow a mutation
and fake a passing check (see the repository's gate traps).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.dont_write_bytecode = True

HUB = "vendor/CMR"
PKG = Path("integrations/paperclip")


def _copy(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def build_tree(target: Path, *, hub: bool = True) -> Path:
    """A scratch repository carrying exactly what the compiler reads."""
    target.mkdir(parents=True, exist_ok=True)
    for relative in (
        "integrations/paperclip",
        # `integrations/paperclip/mapping.py` re-exports the shared seam
        # (`from .._seam.schema import validate`), which issue #1211 extracted to
        # `integrations/_seam/`. Without it in the scratch tree the CLI dies
        # `ModuleNotFoundError: integrations._seam` — which no venue measured
        # while these tests only ever ran where the hub (and so this fixture's
        # `hub=True` path) was absent. See issue #1981.
        "integrations/_seam",
        "governance/modules",
        "scripts/check-module-brief.sh",
        "module.json",
        ".board/snapshot.json",
        "registry/personas/cards/paperclip.yaml",
        "registry/profiles/seeds/paperclip.1.1.0.yaml",
    ):
        source = REPO_ROOT / relative
        if source.exists():
            _copy(source, target / relative)
    if hub:
        if not (REPO_ROOT / HUB / "catalog").is_dir():
            pytest.skip("vendor/CMR submodule not checked out (catalog missing)")
        for relative in ("catalog", "templates", "controller"):
            _copy(REPO_ROOT / HUB / relative, target / HUB / relative)
    for cache in target.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    return target


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def require_real_hub() -> None:
    """Skip a test that reads the real ``vendor/CMR`` submodule when it is an
    uninitialised gitlink placeholder (a `git worktree add` checkout, #1725)."""
    if not (REPO_ROOT / HUB / "catalog").is_dir():
        pytest.skip("vendor/CMR submodule not checked out (catalog missing)")


def null_the_hub_pin(manifest: Path) -> Path:
    """Remove the pin from a vendored module manifest, proving the mutation landed.

    The manifest's own ``versions.latest`` is read and nulled rather than
    string-replaced against a version literal: the pin moves with the hub (it is
    ``v0.2.0`` at the current pin, not the ``v0.1.0`` two provocation sites still
    matched), so a hardcoded literal silently stopped changing anything — and a
    provocation that does not land makes the refusal it was proving vacuous while
    the test still reads as a real control (issue #1981).
    """
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data.get("versions", {}).get("latest"), (
        "{} carries no pin to remove — the provocation would be vacuous".format(manifest)
    )
    data["versions"]["latest"] = None
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8"))["versions"]["latest"] is None, (
        "the mutation did not land"
    )
    return manifest


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Path:
    """Built once; each test copies it so mutations never leak between tests."""
    return build_tree(tmp_path_factory.mktemp("template") / "repo")


@pytest.fixture()
def tree(tmp_path: Path, template: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(template, target, ignore=shutil.ignore_patterns("__pycache__"))
    # The scratch tree is imported by the code under test: a stale bytecode cache
    # there can shadow a mutation and fake a passing check. Prove there is none.
    assert not list(target.rglob("__pycache__"))
    return target


@pytest.fixture(scope="session")
def registry_document(repo_root: Path) -> dict:
    """The registry document the brief is composed from (built in this checkout)."""
    if not (repo_root / HUB / "catalog").is_dir():
        pytest.skip("vendor/CMR submodule not checked out (catalog missing)")
    from governance.modules import registry

    return registry.build(repo_root, repo_root / HUB)


@pytest.fixture(scope="session")
def composition(repo_root: Path, registry_document: dict):
    from integrations.paperclip.reporting import composer

    return composer.compose(registry_document, repo_root, HUB)


#: A declared target that has NOT landed in the pinned catalog, so a venue built
#: with it carries a genuine ``target-pending`` entry. The real tree declares no
#: such target any more — every target it declares has LANDED in the pinned hub,
#: and the catalog is the authority (``governance/modules/registry.py`` derives a
#: landed target as ``registered-mandatory``) — so the real venue carries NO
#: pending entry, and a test whose subject is how pending is *handled* must
#: supply one rather than assert that the authority happens to still carry a
#: pending module (issue #1981). The shape is exactly what the registry reads
#: from ``governance/modules/targets.json``.
UNLANDED_TARGET_SET: dict = {
    "schema": "ao.module-targets/v1",
    "source": "the paperclip/reporting suite's pending-handling venue (issue #1981)",
    "note": (
        "one declared target that has not landed in the pinned catalog, so a "
        "venue built with it carries a genuine target-pending entry"
    ),
    "targets": [
        {
            "id": "suite-pending-module",
            "repo": "kushin77/suite-pending-module",
            "blocking": ["kushin77/CMR#1", "kushin77/suite-pending-module#2"],
            "onboarding": "CMR:ONBOARD-0000",
            "note": "never landed in the catalog — the pending specimen",
        }
    ],
    "watch": [],
}


def declare_a_pending_target(tree: Path) -> Path:
    """Give *tree* one declared target that has not landed, so it carries a pending entry."""
    (tree / "governance" / "modules" / "targets.json").write_text(
        json.dumps(UNLANDED_TARGET_SET, indent=2) + "\n", encoding="utf-8"
    )
    return tree


def build_pending_document(tree: Path) -> dict:
    """The registry document over *tree*, reading *tree*'s OWN declared target set.

    ``targets_path`` is passed explicitly because ``registry.DEFAULT_TARGETS``
    resolves next to the *imported* module: this suite imports
    ``governance.modules`` from the real checkout while pointing the hub and the
    register at a scratch tree, so an implicit read would take the real
    repository's declared targets and the scratch venue's pending entry would
    never appear.
    """
    from governance.modules import registry

    return registry.build(
        tree, tree / HUB, targets_path=tree / "governance" / "modules" / "targets.json"
    )


@pytest.fixture(scope="session")
def pending_template(tmp_path_factory) -> Path:
    """Built once, never mutated: a scratch tree carrying a real ``target-pending`` entry."""
    return declare_a_pending_target(
        build_tree(tmp_path_factory.mktemp("pending-template") / "repo")
    )


@pytest.fixture()
def pending_tree(pending_template: Path, tmp_path: Path) -> Path:
    """A fresh copy of the pending venue, for tests that mutate the tree themselves."""
    target = tmp_path / "repo"
    shutil.copytree(pending_template, target, ignore=shutil.ignore_patterns("__pycache__"))
    assert not list(target.rglob("__pycache__"))
    return target


@pytest.fixture(scope="session")
def pending_document(pending_template: Path) -> dict:
    """The registry document over the pending venue: it really carries a pending entry."""
    return build_pending_document(pending_template)


@pytest.fixture(scope="session")
def pending_composition(pending_template: Path, pending_document: dict):
    """The brief composed over the pending venue, so a pending row is really rendered."""
    from integrations.paperclip.reporting import composer

    return composer.compose(pending_document, pending_template, HUB)


@pytest.fixture()
def document_file(tree: Path) -> Path:
    """A registry document written to disk, so a scratch tree can compose from it."""
    from governance.modules import registry

    path = tree / "registry-document.json"
    path.write_text(
        json.dumps(registry.build(tree, tree / HUB), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def unresolved_document(tree: Path) -> Path:
    """A registry document whose mandatory module claims a seed that is not there.

    Composing from this document makes a *claim* cite a path that resolves
    nowhere under either declared citation base — the "runnable but not truthful"
    case the policy refuses by naming the line (issue #592).
    """
    from governance.modules import registry

    document = registry.build(tree, tree / HUB)
    for entry in document["modules"]:
        if entry["state"] == "registered-mandatory":
            row = entry["assets"][0]
            row["seed"] = "templates/module/not-here.json"
            row["seed_present"] = True
            break
    else:  # pragma: no cover - the registry always carries a mandatory module
        raise AssertionError("no registered-mandatory module to doctor")
    path = tree / "unresolved-registry.json"
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def run_cli(tree: Path, *args: str) -> "subprocess.CompletedProcess":
    """The scratch tree's own CLI, in a fresh interpreter (the lane's tree is never touched)."""
    return subprocess.run(
        [
            sys.executable,
            str(tree / "integrations/paperclip/reporting/cli.py"),
            *args,
            "--repo",
            str(tree),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
