"""Shared fixtures for the ERP module suite (EPIC #645, issue #646).

Every provocation runs against a **scratch copy** of the declaration, never
against the lane's own files: the suite proves the validator's refusals bite, and
a suite that mutates the tree it is judging would be its own worst gate.

``sys.dont_write_bytecode`` is set and every ``__pycache__`` under the scratch
tree is removed before it is read: a stale bytecode file can shadow a mutation
and fake a passing check (see the repository's gate traps).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

#: This file is <root>/integrations/erp/catalog/tests/conftest.py.
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.dont_write_bytecode = True

from integrations.erp.catalog import model  # noqa: E402


def _copy(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _strip_bytecode(root: Path) -> None:
    for path in sorted(root.rglob("__pycache__"), reverse=True):
        shutil.rmtree(path, ignore_errors=True)


def build_tree(target: Path) -> Path:
    """A scratch repository carrying exactly what the validator reads.

    The catalogue's own schemas and every declared document, the indexer's source
    registry, the epic's gap analysis, and the docs index — plus a README per
    catalogued pillar directory, because the module map must name real pillars.
    """
    target.mkdir(parents=True, exist_ok=True)
    _copy(REPO_ROOT / model.MODULE_ROOT, target / model.MODULE_ROOT)
    _copy(REPO_ROOT / model.SOURCES_PATH, target / model.SOURCES_PATH)
    _copy(REPO_ROOT / model.GAPS_PATH, target / model.GAPS_PATH)
    for readme in sorted(REPO_ROOT.glob("*/README.md")):
        relative = readme.relative_to(REPO_ROOT)
        _copy(readme, target / relative)
    _strip_bytecode(target)
    return target


def rewrite(path: Path, old: str, new: str) -> None:
    """Replace every ``old`` with ``new``, refusing a provocation whose anchor is absent.

    A mutation that silently changed nothing would leave the check green and the
    "provocation" vacuous, which is the failure mode this whole file exists to
    avoid.
    """
    text = path.read_text(encoding="utf-8")
    assert old in text, "the provocation's anchor %r is not in %s" % (old, path)
    changed = text.replace(old, new)
    assert changed != text, "the provocation did not change %s" % path
    path.write_text(changed, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, document: Any) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def catalogue_dir(tree: Path) -> Path:
    return tree / model.CATALOGUE_ROOT


def documents_dir(tree: Path) -> Path:
    return catalogue_dir(tree) / "documents"


@pytest.fixture()
def scratch(tmp_path: Path) -> Path:
    """A throwaway copy of the module's declaration, safe to mutate."""
    return build_tree(tmp_path / "repo")
