"""Pytest bootstrap for the governance/sync suite (issue #44).

The modules under governance/sync/ are standalone scripts with no package
__init__.py (mirroring governance/merge and registry/personas). Inserting the
package directory at the front of sys.path lets the tests import them plainly
as ``provenance``, ``drift``, ``blast_radius`` and ``sync_plan``.
"""

from __future__ import annotations

import os
import sys

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

import synchelpers  # noqa: E402


@pytest.fixture
def ecosystem(tmp_path):
    """Build the offline ecosystem; return (canonical_root, consumers).

    ``consumers`` maps consumer_id -> (local_root, ProvenanceManifest).
    """
    canonical_root, consumers = synchelpers.build_ecosystem(tmp_path)
    return canonical_root, consumers


@pytest.fixture
def canonical_root(tmp_path):
    return synchelpers.build_canonical(tmp_path / "canonical")
