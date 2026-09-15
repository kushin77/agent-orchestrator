"""The GR-10 harvest record, and the control that keeps it honest (issue #650)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.erp.crm import provenance
from integrations.erp.crm.model import Refused

HERE = Path(__file__).resolve().parent.parent


def test_the_shipped_record_loads_and_names_its_policy() -> None:
    record = provenance.load()
    assert record.policy == provenance.POLICY
    assert record.module == "integrations/erp/crm"
    assert record.schema_version == 1


def test_the_shipped_record_covers_the_four_harvested_shapes() -> None:
    record = provenance.load()
    assert record.shapes() == (
        "crm-funnel",
        "projects-timesheet-accumulation",
        "inspection-outcome-transitions",
        "support-sla-ageing",
    )


def test_every_harvest_records_where_it_came_from_and_under_which_licence() -> None:
    for harvest in provenance.load().harvests:
        assert harvest.upstream == "frappe/erpnext"
        assert harvest.license == "GPL-3.0"
        assert harvest.url.startswith("https://")
        assert harvest.upstream_version
        assert len(harvest.harvested_at) >= 10
        assert harvest.note


def test_no_harvest_claims_copied_upstream_code() -> None:
    """ERPNext is GPL-3.0: harvesting shapes is permitted, copying code is not."""
    assert [harvest.code_copied for harvest in provenance.load().harvests] == [False] * 4


def test_the_record_is_json_and_round_trips() -> None:
    path = HERE / "catalog" / "provenance.json"
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    assert provenance.load(provenance.load().to_dict()).to_dict() == provenance.load().to_dict()


def test_a_harvest_that_claims_copied_code_is_refused_by_name() -> None:
    """The constraint is checked, not trusted."""
    record = provenance.load().to_dict()
    record["harvests"][0]["codeCopied"] = True
    with pytest.raises(Refused, match="code-copied") as caught:
        provenance.load(record)
    assert "crm-funnel" in caught.value.detail
    assert "GPL-3.0" in caught.value.detail


def test_a_record_with_no_harvests_is_refused_by_name() -> None:
    """An empty provenance file is an unfilled form: it would let a lane harvest silently."""
    record = provenance.load().to_dict()
    record["harvests"] = []
    with pytest.raises(Refused, match="provenance-invalid") as caught:
        provenance.load(record)
    assert "at least one harvest" in caught.value.detail


def test_a_harvest_without_a_licence_is_refused() -> None:
    record = provenance.load().to_dict()
    del record["harvests"][0]["license"]
    with pytest.raises(Refused, match="provenance-invalid") as caught:
        provenance.load(record)
    assert "license" in caught.value.detail


def test_a_harvest_without_a_url_is_refused() -> None:
    record = provenance.load().to_dict()
    record["harvests"][0]["url"] = "ftp://example.invalid"
    with pytest.raises(Refused, match="provenance-invalid") as caught:
        provenance.load(record)
    assert "url" in caught.value.detail


def test_a_record_that_weakens_the_policy_is_refused() -> None:
    record = provenance.load().to_dict()
    record["policy"] = "anything-goes"
    with pytest.raises(Refused, match="provenance-invalid") as caught:
        provenance.load(record)
    assert "policy" in caught.value.detail


def test_two_harvests_of_one_shape_are_refused() -> None:
    record = provenance.load().to_dict()
    record["harvests"][1]["shape"] = record["harvests"][0]["shape"]
    with pytest.raises(Refused, match="provenance-invalid") as caught:
        provenance.load(record)
    assert "more than once" in caught.value.detail


def test_a_missing_or_unreadable_record_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="provenance-invalid") as caught:
        provenance.load(tmp_path / "absent.json")
    assert "not found" in caught.value.detail

    broken = tmp_path / "provenance.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(Refused, match="provenance-invalid"):
        provenance.load(broken)


def test_the_provenance_markdown_names_the_constraint() -> None:
    """The human record and the machine record agree about the licence and the rule."""
    text = (HERE / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "GPL-3.0" in text
    assert "no upstream code is copied" in text.lower() or "not copied" in text.lower()
