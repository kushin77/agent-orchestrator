"""The cloudflare policy domain's registration (issue #1766).

The Cloudflare edge policy is the `cloudflare` domain: its declared source of
truth is `docs/CLOUDFLARE-POLICY.md`, and it is registered by the ONE file
`governance/policy/domains/cloudflare.yaml` — the one-file-per-domain contract of
`governance/policy/registry.py` (#1763). These tests read the REAL repository
root, so they fail if the domain file, its declared source, or its visibility is
ever dropped.

No network, no repository mutation: every assertion reads the real tree, and the
``sys.path`` bootstrap is the shared ``conftest.py`` idiom in this directory.
"""

from __future__ import annotations

from pathlib import Path

from governance.policy.registry import PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]

SOURCE_FILE = "docs/CLOUDFLARE-POLICY.md"


def _cloudflare_row():
    rows = {row.domain: row for row in PolicyRegistry(REPO_ROOT).rows()}
    assert "cloudflare" in rows, (
        "governance/policy/domains/cloudflare.yaml must register the cloudflare domain"
    )
    return rows["cloudflare"]


def test_cloudflare_domain_declares_the_policy_spec_as_its_source():
    assert _cloudflare_row().source_file == SOURCE_FILE


def test_cloudflare_domain_is_control_plane_visible():
    assert _cloudflare_row().control_plane_visible is True


def test_cloudflare_declared_source_exists():
    """A visibility that survives a missing source would be a false green."""
    assert (REPO_ROOT / SOURCE_FILE).is_file(), f"{SOURCE_FILE} is the declared source"
