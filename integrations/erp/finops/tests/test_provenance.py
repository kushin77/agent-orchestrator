"""GR-10: the harvest record is enforced, not trusted (issue #654)."""

from __future__ import annotations

import pytest

from integrations.erp.finops import provenance
from integrations.erp.finops.model import Refused


def _record(**overrides):
    entry = {
        "shape": "a-shape",
        "upstream": "frappe/erpnext",
        "upstreamVersion": "v16 docs",
        "license": "GPL-3.0",
        "codeCopied": False,
        "url": "https://example.invalid/x",
        "harvestedAt": "2026-09-15",
    }
    payload = {
        "schemaVersion": "ao.erp.finops/v1",
        "module": "integrations/erp/finops",
        "policy": provenance.POLICY,
        "harvests": [entry],
    }
    payload.update(overrides)
    return payload


def test_the_shipped_record_loads_and_claims_no_copied_code() -> None:
    record = provenance.load()
    assert record.policy == provenance.POLICY
    assert record.module == "integrations/erp/finops"
    assert len(record.harvests) >= 4
    for harvest in record.harvests:
        assert harvest.code_copied is False
        assert harvest.license
        assert harvest.url


def test_the_shipped_record_declares_its_sources_with_a_url() -> None:
    record = provenance.load()
    upstreams = record.upstreams()
    assert any("erpnext" in upstream for upstream in upstreams), (
        "the ERPNext pattern source must be recorded"
    )
    assert any("agent-orchestrator" in upstream for upstream in upstreams), (
        "the in-repo harvest must be recorded with the same discipline"
    )


def test_a_harvest_claiming_copied_code_is_refused() -> None:
    payload = _record()
    payload["harvests"][0]["codeCopied"] = True
    with pytest.raises(Refused) as caught:
        provenance.load(payload)
    assert caught.value.code == "provenance-code-copied"


def test_an_empty_record_is_refused() -> None:
    with pytest.raises(Refused) as caught:
        provenance.load(_record(harvests=[]))
    assert caught.value.code == "provenance-empty"


def test_a_record_that_does_not_meet_its_schema_is_refused() -> None:
    payload = _record()
    del payload["module"]
    with pytest.raises(Refused) as caught:
        provenance.load(payload)
    assert caught.value.code == "provenance-invalid"
    assert "module" in caught.value.detail


def test_a_weaker_policy_is_refused() -> None:
    with pytest.raises(Refused) as caught:
        provenance.load(_record(policy="copy-anything"))
    assert caught.value.code == "provenance-invalid"
    assert "policy" in caught.value.detail


def test_an_unreadable_file_is_refused_rather_than_treated_as_empty(tmp_path) -> None:
    path = tmp_path / "not-json.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(Refused) as caught:
        provenance.load(path)
    assert caught.value.code == "provenance-invalid"
