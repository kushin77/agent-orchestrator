"""Pytest bootstrap for the governance/authority suite (issue #150).

The modules under ``governance/authority/`` are standalone files with no package
``__init__.py`` (the convention shared with governance/merge, conformance,
dispatch and knowledge). Putting the package directory on ``sys.path`` lets the
tests import ``model``, ``cli`` and ``isolation`` plainly.
"""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

PKG_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

# governance/authority shares the bare basenames "model" and "cli" with several
# sibling governance/* suites (board, dispatch, lessons, ticket, waves, ...).
# When this suite is collected alongside them in one pytest invocation, a bare
# ``import model``/``import cli`` earlier in collection wins the shared
# sys.modules slot for the rest of the run (issues #699, #702, #1042). Evict
# any stale entry immediately before this package's own bare imports so the
# fresh lookup (against the sys.path entry just inserted above) resolves to
# THIS package's files.
#
# "isolation" must be evicted too, not just "model"/"cli": isolation.py does
# its own bare `import model`, so if an earlier-collected test file within
# THIS suite already cached "isolation" (from a stale sys.path/module state)
# while this eviction only cleared "model", a later `import model` here would
# hand this file a fresh `model.can_act`/class objects while the cached
# `isolation` module keeps referencing the OLD ones — two distinct object
# identities. `monkeypatch.setattr(model, "can_act", ...)` then patches a
# name `isolation.py`'s own copy never reads, so the negative-control test
# quietly measures the real (unpatched) behaviour instead of the planted one
# (#1501).
for _name in ("model", "cli", "isolation"):
    sys.modules.pop(_name, None)

import model  # noqa: E402  (import after the sys.path bootstrap on purpose)

SHA_A = "0123456789abcdef0123456789abcdef01234567"
SHA_B = "fedcba9876543210fedcba9876543210fedcba98"


@pytest.fixture(scope="session")
def package_dir() -> Path:
    return PKG_DIR


@pytest.fixture(scope="session")
def matrix() -> model.Matrix:
    """The shipped matrix, loaded once (it is validated by the suite itself)."""
    return model.load_matrix()


@pytest.fixture(scope="session")
def document(matrix: model.Matrix) -> dict:
    """A deep copy of the shipped matrix document, safe for overlay surgery."""
    return deepcopy(dict(matrix.document))


@pytest.fixture(scope="session")
def schema_path() -> Path:
    return PKG_DIR / "schema.json"


def evidence(
    repo: str,
    *,
    command: str = "bash scripts/check-authority.sh",
    output: str = "check-authority: OK — matrix valid, controls met",
    exit_code: int = 0,
    git_sha: str = SHA_A,
    recorded_by: str = "ao-scribe-1",
) -> dict:
    """A complete, real-shaped evidence record (tests override one field at a time)."""
    return {
        "repo": repo,
        "command": command,
        "output": output,
        "exit_code": exit_code,
        "git_sha": git_sha,
        "recorded_by": recorded_by,
    }


def with_evidence(document: dict, item_id: str, **overrides) -> dict:
    """Return a document whose work item carries both evidence records.

    ``overrides`` apply to the gate record; the verify record mirrors the
    exit code, commit and recorder so the two always describe one commit unless a
    test explicitly diverges them afterwards.
    """
    patched = deepcopy(document)
    for item in patched["work_items"]:
        if item["id"] != item_id:
            continue
        item["head_sha"] = overrides.pop("head_sha", SHA_A)
        item["gate_evidence"] = evidence(item["repo"], **overrides)
        item["verify_evidence"] = evidence(
            item["repo"],
            command="make verify",
            output="verify: PASS (28 of 28 checks)",
            exit_code=item["gate_evidence"]["exit_code"],
            git_sha=item["gate_evidence"]["git_sha"],
            recorded_by=item["gate_evidence"]["recorded_by"],
        )
    return patched
