"""The `git` policy domain is registered and visible (issue #1765).

This is the domain-lane sibling of ``test_registry.py`` (#1763): the registry
module's own contract is proven there, and here we only assert that the domain
this lane adds — ``governance/policy/domains/git.yaml`` — appears as a row in the
REAL repository and is stood behind (visible, pointing at the declared capture).

Import idiom mirrors ``conftest.py``/``test_registry.py``: the repository root is
put on ``sys.path`` by the package conftest, so the module is imported by its
real dotted path rather than a bare basename a sibling suite could shadow.
"""

from __future__ import annotations

from pathlib import Path

from governance.policy.registry import PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]

SOURCE_FILE = ".github/BRANCH-PROTECTION.md"


def _rows_by_domain() -> dict:
    return {row.domain: row for row in PolicyRegistry(repo_root=REPO_ROOT).rows()}


def test_git_domain_is_registered():
    """The git domain this lane adds appears in the registry's rows."""
    rows = _rows_by_domain()
    assert "git" in rows, (
        "governance/policy/domains/git.yaml must register the 'git' domain "
        "(the filename stem is the domain name)"
    )


def test_git_domain_is_control_plane_visible_and_names_the_capture():
    """The git row is stood behind: visible, and pointing at the declared capture."""
    row = _rows_by_domain()["git"]
    assert row.control_plane_visible is True, (
        "the git domain's declared source must exist so the registry stands behind it"
    )
    assert row.source_file == SOURCE_FILE, (
        f"the git domain's source_file must be {SOURCE_FILE}"
    )
    assert row.enforcement_point, "the git row must name where the policy bites"
