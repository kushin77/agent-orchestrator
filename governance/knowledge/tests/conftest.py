"""Pytest bootstrap + a self-satisfying fixture tree for governance/knowledge.

The modules under governance/knowledge/ are standalone files with no package
``__init__.py`` (the convention shared with governance/merge, governance/sync and
governance/dispatch). Putting the package directory on sys.path lets the tests
import them plainly.

The fixture tree is generated **from the catalogue itself**, so adding a required
source in `sources.py` cannot silently leave the tests testing a stale shape.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

from sources import SOURCE_SPECS, required_specs  # noqa: E402

SNAPSHOT = {
    "generated_at": "2026-09-13T00:00:00Z",
    "source": "kushin77/agent-orchestrator",
    "issues": [
        {
            "number": 139,
            "title": "Implement the CMR/GDC institutional knowledge indexer",
            "state": "OPEN",
            "milestone": "M24 - Enterprise Knowledge Index",
            "labels": ["type:feature", "area:knowledge-index"],
            "parent": 138,
            "blocked_by": [137],
        },
        {
            "number": 140,
            "title": "Enforce CMR class conformance across repos",
            "state": "CLOSED",
            "milestone": "M24 - Enterprise Knowledge Index",
            "labels": ["type:governance"],
            "parent": 138,
            "blocked_by": [],
        },
    ],
}


def sample_path(pattern: str) -> str:
    """A concrete path that satisfies a catalogue glob."""
    parts = []
    for part in pattern.split("/"):
        if part == "**":
            parts.append("sample")
        elif "*" in part:
            parts.append(part.replace("*", "sample"))
        else:
            parts.append(part)
    return "/".join(parts)


def make_tree(root: Path, *, text: str = "# Sample\n\nA governance asset.\n") -> Path:
    """Materialise every required source under ``root``."""
    root = Path(root)
    for spec in required_specs():
        relative = sample_path(spec.pattern)
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".json") and relative == spec.pattern:
            path.write_text(json.dumps(SNAPSHOT, indent=2) + "\n", encoding="utf-8")
        else:
            path.write_text(text, encoding="utf-8")
    # The board snapshot is required for issue metadata regardless of provenance.
    snapshot = root / ".board" / "snapshot.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(SNAPSHOT, indent=2) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A repo tree that satisfies every required catalogue source."""
    return make_tree(tmp_path)


@pytest.fixture
def catalog_payload(tree: Path) -> dict:
    """A recorded catalogue payload with one item, for drift tests."""
    return {
        "schema": "cmr.knowledge/index-v1",
        "generated_at": "2026-09-13T00:00:00Z",
        "repo": "kushin77/agent-orchestrator",
        "items": [
            {
                "id": "AGENTS.md",
                "kind": "governance",
                "path": "AGENTS.md",
                "title": "Agents",
                "provenance": {"sha256": "0" * 64, "version": "deadbeef"},
            }
        ],
    }


def credentialed_literal() -> str:
    """A credential-shaped value assembled at runtime.

    Never written literally in this file: the repository's own secret scan greps
    the tracked source text, so a literal here would fail `make verify` for a
    reason that has nothing to do with a real secret.
    """
    return "sk" + "-" + ("A" * 32)


def credential_assignment() -> str:
    """A credential-shaped assignment, assembled at runtime (see above)."""
    return ("api" + "_" + "key") + "=" + ("Z" * 30)


def credential_line(word: str = "token") -> str:
    """``<secret-word> = <credential>`` assembled at runtime.

    Spelled contiguously in source this trips the repository's own generic
    credential rule, which greps tracked text for a secret word followed by an
    assignment of a quoted value — the quoted remainder of the line reads as the
    secret. Passing the word in keeps the source text from ever containing that
    shape.
    """
    return word + " = " + credentialed_literal()


ALL_SPEC_PATTERNS = tuple(spec.pattern for spec in SOURCE_SPECS)
