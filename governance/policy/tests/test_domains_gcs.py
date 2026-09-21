"""The ``gcs`` policy domain is registered and points at the real .tf source (#1767).

Reads the REAL repository root (like ``test_registry.py``'s real-repo cases):
the point of this lane is that the GCS policy declared in
``infra/terraform/main.tf`` is reachable through the registry, and that the
declared ``source_file`` is a file that actually exists. No fixtures, no
mutation, no network — the ``sys.path`` bootstrap is ``conftest.py``'s, reused
by every module in this suite.
"""

from __future__ import annotations

from pathlib import Path

from governance.policy.registry import PolicyRegistry, PolicyRow

REPO_ROOT = Path(__file__).resolve().parents[3]

GCS_SOURCE_FILE = "infra/terraform/main.tf"


def _gcs_row() -> PolicyRow:
    rows = {row.domain: row for row in PolicyRegistry(REPO_ROOT).rows()}
    assert "gcs" in rows, "the gcs domain must be registered in governance/policy/domains/"
    return rows["gcs"]


def test_gcs_domain_is_registered():
    assert _gcs_row().domain == "gcs"


def test_gcs_domain_is_control_plane_visible():
    assert _gcs_row().control_plane_visible is True


def test_gcs_source_file_is_the_terraform_entrypoint():
    assert _gcs_row().source_file == GCS_SOURCE_FILE


def test_gcs_declared_source_file_exists():
    """The registry must be standing behind a real file, not a refusal row."""
    row = _gcs_row()
    assert (REPO_ROOT / row.source_file).is_file()
    assert (REPO_ROOT / GCS_SOURCE_FILE).is_file()
    # A refusal row would name an absent path; this one points at the .tf file.
    assert row.enforcement_point.startswith("not reporting:") is False


def test_gcs_enforcement_point_names_the_apply_pipeline():
    """AO-GR-5: the policy bites at the flag-gated apply, never an ad-hoc apply."""
    point = _gcs_row().enforcement_point
    assert "apply.yaml" in point
    assert "AO-GR-5" in point
