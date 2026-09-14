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
        for relative in ("catalog", "templates", "controller"):
            _copy(REPO_ROOT / HUB / relative, target / HUB / relative)
    for cache in target.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    return target


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


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
    from governance.modules import registry

    return registry.build(repo_root, repo_root / HUB)


@pytest.fixture(scope="session")
def composition(repo_root: Path, registry_document: dict):
    from integrations.paperclip.reporting import composer

    return composer.compose(registry_document, repo_root, HUB)


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
