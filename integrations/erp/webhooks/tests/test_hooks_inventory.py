"""Cross-check: every PF-2 hook has a disposition here (#671 acceptance).

Parses the "CRM conversion-hook inventory" table out of
``docs/erp-finops/token-baseline.md`` (PF-2, #667) and asserts every row's
"Hook" column has a matching entry in
``integrations.erp.webhooks.hooks.PF2_DISPOSITIONS`` — so a future edit to
PF-2 that adds a hook without adding a disposition here fails this suite
instead of silently under-covering the criterion.
"""

from __future__ import annotations

from pathlib import Path

from integrations.erp.webhooks import hooks as hooks_module

REPO_ROOT = Path(__file__).resolve().parents[4]
BASELINE_DOC = REPO_ROOT / "docs" / "erp-finops" / "token-baseline.md"


def _pf2_hook_rows() -> list:
    """Every "Hook" cell in the inventory table, in source order."""
    text = BASELINE_DOC.read_text(encoding="utf-8")
    section = text.split("## CRM conversion-hook inventory", 1)[1]
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0] in ("Hook",):
            continue
        rows.append(cells[0])
    return rows


def test_baseline_doc_exists():
    assert BASELINE_DOC.is_file(), f"expected PF-2 baseline at {BASELINE_DOC}"


def test_every_table_row_is_a_disposition_key():
    rows = _pf2_hook_rows()
    assert rows, "expected at least one hook row in the PF-2 inventory table"
    for row in rows:
        assert row in hooks_module.PF2_DISPOSITIONS, (
            f"PF-2 lists hook {row!r} with no disposition in "
            f"integrations/erp/webhooks/hooks.py"
        )


def test_every_disposition_has_a_reason():
    for key, disposition in hooks_module.PF2_DISPOSITIONS.items():
        assert disposition.reason.strip(), f"{key!r} disposition carries no reason"
        assert disposition.disposition in hooks_module.DISPOSITIONS


def test_external_crm_item_is_dispositioned():
    # PF-2's CANNOT-ASSESS row is prose, not a table row; assert it is
    # covered explicitly rather than by table-parsing.
    assert "External CRM conversion hooks" in hooks_module.PF2_DISPOSITIONS
