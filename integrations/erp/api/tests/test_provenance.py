"""The GR-10 harvest record and its enforcement (#651).

The lane cannibalizes two surfaces and consumes two modules, and GR-10 says a
harvest records its source. The record is only worth having if it can be *wrong*,
so these tests provoke the two ways it can lie: a harvest that admits to a copy,
and a harvest that cannot say what it built instead.
"""

from __future__ import annotations

import json

import pytest

from integrations.erp.api import provenance
from integrations.erp.auth.model import Refused


def _record(**over) -> dict:
    record = {
        "schema": provenance.SCHEMA,
        "upstreamDomainSource": "frappe/erpnext (GPL-3.0), declared in the module manifest",
        "harvests": [
            {
                "shape": "a shape",
                "mode": "pattern-only",
                "source": {"repo": "kushin77/agent-orchestrator", "path": "a/b.py", "license": "see LICENSE"},
                "builtInstead": "something built here instead",
            }
        ],
    }
    record.update(over)
    return record


def test_the_shipped_record_holds():
    record = provenance.load()
    assert record["schema"] == provenance.SCHEMA
    assert provenance.check(record) == ()
    assert len(record["harvests"]) >= 4, "every cannibalized surface and consumed module is recorded"
    assert record["policy"]["codeCopied"] is False


def test_the_record_is_committed_and_parseable():
    path = provenance.CATALOG / provenance.RECORD_PATH.name
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == provenance.SCHEMA


def test_the_record_points_at_the_domain_declaration_rather_than_restating_it():
    """One declaration, no second copy to drift: the GPL upstream lives in the manifest."""
    record = provenance.load()
    assert "module.yaml" in record["upstreamDomainSource"]
    assert "GPL" in record["upstreamDomainSource"]


@pytest.mark.parametrize("mode", ["copied", "vendored", "translated"])
def test_a_harvest_that_admits_to_a_copy_is_refused(mode):
    record = _record()
    record["harvests"][0]["mode"] = mode
    findings = provenance.check(record)
    assert any("a copy is not a harvest" in finding for finding in findings), findings
    with pytest.raises(Refused) as caught:
        provenance.load(record)
    assert caught.value.code == "harvest-code-copied"


def test_a_harvest_with_no_mode_is_refused_as_incomplete():
    """A missing mode is *missing*, not a copy — the code must follow the rule, not the words."""
    record = _record()
    del record["harvests"][0]["mode"]
    findings = provenance.check(record)
    assert any("missing 'mode'" in finding for finding in findings), findings
    with pytest.raises(Refused) as caught:
        provenance.load(record)
    assert caught.value.code == "harvest-incomplete"


def test_a_harvest_that_claims_its_own_mode_is_refused():
    record = _record()
    record["harvests"][0]["mode"] = ""
    with pytest.raises(Refused) as caught:
        provenance.load(record)
    assert caught.value.code == "harvest-incomplete"
    assert "missing 'mode'" in caught.value.detail


@pytest.mark.parametrize("missing", ["shape", "mode", "source", "builtInstead"])
def test_an_incomplete_harvest_is_refused(missing):
    record = _record()
    del record["harvests"][0][missing]
    with pytest.raises(Refused) as caught:
        provenance.load(record)
    assert caught.value.code == "harvest-incomplete"


def test_a_harvest_without_a_source_is_refused():
    record = _record()
    record["harvests"][0]["source"] = {"repo": "kushin77/agent-orchestrator"}
    with pytest.raises(Refused) as caught:
        provenance.load(record)
    assert caught.value.code == "harvest-incomplete"
    assert "path" in caught.value.detail


def test_an_empty_record_documents_nothing():
    with pytest.raises(Refused) as caught:
        provenance.load(_record(harvests=[]))
    assert caught.value.code == "harvest-incomplete"
    assert "documents nothing" in caught.value.detail


def test_a_restated_upstream_declaration_is_refused():
    record = _record()
    del record["upstreamDomainSource"]
    with pytest.raises(Refused) as caught:
        provenance.load(record)
    assert caught.value.code == "harvest-incomplete"
    assert "never restated" in caught.value.detail


def test_a_wrong_schema_marker_is_refused():
    with pytest.raises(Refused) as caught:
        provenance.load(_record(schema="something-else/v9"))
    assert caught.value.code == "declaration-invalid"


def test_an_absent_record_is_refused(tmp_path):
    with pytest.raises(Refused) as caught:
        provenance.load(tmp_path / "nowhere.json")
    assert caught.value.code == "declaration-invalid"


def test_the_consumed_modules_are_recorded_as_consumed():
    """An imported module is `consumed`, never `pattern-only` — the distinction is the point."""
    modes = {harvest["mode"] for harvest in provenance.load()["harvests"]}
    assert modes == {"pattern-only", "consumed"}
    assert all(mode in provenance.MODES for mode in modes)
