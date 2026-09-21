"""The vendor-cmr domain (#1769) — the CMR GDC claim, measured and cross-referenced.

The owner's premise ("our CMR already has a GDC policy") was recorded as a gap in
``docs/rca/2026-09-21-policy-centralization-gap-review.md`` on the strength of
``grep -rln "gdc" vendor/CMR/`` returning nothing — a VACUOUS measurement, since
``vendor/CMR`` is an unpopulated submodule in every fresh worktree (0 files;
``git submodule status`` prints a leading ``-``), so the grep returns zero hits
whatever CMR contains. Measured instead at the pin the repo declares
(``.gitmodules`` -> kushin77/CMR @ b6c49aa…), that ref carries the GDC policy
surface (``sync/gdc-policy-bundle.txt`` and friends).

These assertions hold the registry row to what was actually measured: the domain
is present, it is NOT reported control-plane visible (the vendored content is not
materialised, so the registry cannot stand behind a local source), and its reason
names both the unpopulated local submodule (the observed absence of local
content) and the confirmed artifact — so the residual open item is the
reconciliation question, never an asserted absence.

``conftest.py`` puts the repository root on ``sys.path``; the module is imported
by its real dotted path, exactly as ``test_registry.py`` does.
"""

from __future__ import annotations

from pathlib import Path

from governance.policy.registry import PolicyRegistry, PolicyRow

REPO_ROOT = Path(__file__).resolve().parents[3]


def _row(domain: str) -> PolicyRow:
    rows = {row.domain: row for row in PolicyRegistry(REPO_ROOT).rows()}
    return rows[domain]


def test_vendor_cmr_domain_is_registered():
    rows = {row.domain for row in PolicyRegistry(REPO_ROOT).rows()}
    assert "vendor-cmr" in rows, "the #1769 domain must appear as a row"


def test_vendor_cmr_is_not_control_plane_visible():
    """The gap is DECLARED, not hidden: present as a row, not reported visible."""
    assert _row("vendor-cmr").control_plane_visible is False


def test_vendor_cmr_source_is_the_declared_vendored_path():
    row = _row("vendor-cmr")
    assert row.source_file == "vendor/CMR"
    # The registry only stands behind a declared source that exists; a row whose
    # source is absent would instead be a refusal. This one names a real path.
    assert (REPO_ROOT / row.source_file).exists()


def test_vendor_cmr_reason_names_the_unpopulated_submodule_and_the_pin():
    """The reason names (a) the observed LOCAL absence and (b) the measured pin."""
    reason = _row("vendor-cmr").enforcement_point

    # (a) the observed local state — the submodule is not checked out, so the
    #     RCA's grep could not have seen CMR's contents.
    assert "unpopulated" in reason

    # (b) the pin the repo declares and the artifact actually found at it — the
    #     cross-reference that replaces the RCA's unsupported "Not found".
    assert "b6c49aa" in reason
    assert "sync/gdc-policy-bundle.txt" in reason


def test_vendor_cmr_reason_states_a_cross_reference_not_an_absence():
    """It is framed as a confirmed cross-reference + an open reconciliation."""
    reason = _row("vendor-cmr").enforcement_point
    assert "cross-reference" in reason
    assert "reconcil" in reason
