"""The git-ecosystem registrations (issue #626) — and the control that makes them real.

Registering a source is worth something only if the index fails when the asset the
entry names disappears; otherwise the entry is a comment written in Python, not a
constraint. The first two tests pin the acceptance criteria of issue #626 (every
named artifact registered, each with a kind drawn from the closed vocabulary and an
owner); the third is the negative control that proves the registration constrains
the tree rather than merely documenting it.
"""

from __future__ import annotations

from pathlib import Path

from indexer import build_index
from model import CODE_REQUIRED_SOURCE_MISSING, KINDS, errors
from sources import SOURCE_SPECS

# Every artifact issue #626 names, with the kind it is registered under. The
# vocabulary is closed (ADR-0018 / `KINDS`) so each of these must be an existing
# kind: the catalogue is the home of the seven institutional kinds, and a new one
# would have to be declared there first.
GIT_SOURCES = {
    ".github/ISSUE_TEMPLATE/**": "pattern-template",
    ".github/PULL_REQUEST_TEMPLATE.md": "pattern-template",
    ".gitmessage": "pattern-template",
    "docs/GIT-TEMPLATES-GAP-ANALYSIS.md": "governance",
    "docs/SHELL-PATTERNS.md": "pattern-template",
    "docs/GIT-ENV-VARIABLES.md": "governance",
}


def test_the_git_ecosystem_artifacts_are_registered_with_kind_and_owner():
    """ACCEPTANCE (#626): each named artifact has a spec, a kind and an owner."""
    registered = {spec.pattern: spec for spec in SOURCE_SPECS}

    missing = sorted(pattern for pattern in GIT_SOURCES if pattern not in registered)
    assert missing == [], "unregistered git-ecosystem source(s): %s" % ", ".join(missing)

    for pattern, kind in GIT_SOURCES.items():
        spec = registered[pattern]
        assert spec.kind == kind, "%s is registered under an unexpected kind" % pattern
        assert spec.kind in KINDS, "kind must be drawn from the closed vocabulary"
        assert spec.owner, "an unowned source is a rumour-store entry"


def test_the_sources_whose_owning_lane_has_not_landed_are_optional():
    """issue #626's "(after its lane lands)" is a measured state, not a wish.

    `docs/SHELL-PATTERNS.md` (lane #621) and `docs/GIT-ENV-VARIABLES.md` (lane
    #627) do not exist yet. Requiring either would fail the gate by name on work
    this lane does not owe; declaring them optional means the owning lane's merge
    joins the index on the next build, with no second edit to `sources.py`.
    """
    registered = {spec.pattern: spec for spec in SOURCE_SPECS}

    for pattern in ("docs/SHELL-PATTERNS.md", "docs/GIT-ENV-VARIABLES.md"):
        assert registered[pattern].required is False


def test_deleting_a_registered_git_artifact_is_a_named_error(tree: Path):
    """NEGATIVE CONTROL: the registration constrains the tree."""
    (tree / ".gitmessage").unlink()

    index = build_index(tree, owner_repo="acme/widgets")

    finding = next(
        f
        for f in index.findings
        if f.code == CODE_REQUIRED_SOURCE_MISSING and f.path == ".gitmessage"
    )
    assert ".gitmessage" in finding.message
    assert errors(index.findings)
