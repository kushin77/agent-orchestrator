"""The local-dev domain's registration — AO-GR-17, hub GR-17 (issue #1768).

A domain is registered by exactly ONE file whose filename stem IS the domain
name (``governance/policy/README.md``), so #1768's contract is one declaration
plus its row: the domain appears, it reads as control-plane visible, and the
``source_file`` it names really exists under the repository root.

These assertions read the REAL repository root — the way ``test_registry.py``'s
``test_real_repo_registers_gdc_and_isolation`` does — so a domain that stops
being declared fails here rather than only in a staging fixture. The ``sys.path``
bootstrap is this suite's own ``conftest.py`` (the #1763 idiom), so the module is
imported by its real dotted path and no sibling ``governance/*`` suite can shadow
it.
"""

from __future__ import annotations

from pathlib import Path

from governance.policy.registry import PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]

DOMAINS_RELATIVE = Path("governance") / "policy" / "domains"
DECLARATION = DOMAINS_RELATIVE / "local-dev.yaml"


def _row():
    """The ``local-dev`` row from the real repository, or ``None`` if absent."""
    rows = {row.domain: row for row in PolicyRegistry(REPO_ROOT).rows()}
    return rows.get("local-dev")


def test_one_file_registers_the_local_dev_domain():
    """The declaration is exactly one file, named for the domain it registers."""
    assert DECLARATION.is_file()
    assert _row() is not None


def test_local_dev_domain_is_control_plane_visible():
    """Visible, and not a refusal row — a refusal names the reason it cannot stand."""
    row = _row()
    assert row is not None
    assert row.control_plane_visible is True
    assert not row.enforcement_point.startswith("not reporting:")


def test_local_dev_source_file_is_agents_md_and_exists():
    """The declared source of truth is ``AGENTS.md``, and it is really on disk."""
    row = _row()
    assert row is not None
    assert row.source_file == "AGENTS.md"
    assert (REPO_ROOT / row.source_file).is_file()
