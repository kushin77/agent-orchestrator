"""Diagrams blueprint projection tests (issue #465, ADR-0017).

Every test drives the adapter through the seam ``FixtureTransport`` — the same
transport the canonical client carries — so the projection is exercised offline
and deterministically. The suite proves the frozen shape (a ticket's structured
``evidence[]``, no facet, no new ticket kind), that the output is byte-stable,
and that each refusal really refuses *by name* — most importantly, that a
Finding whose declared and live attributes are equal is never reported as drift.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from integrations.paperclip import diagrams as diagrams_mod
from integrations.paperclip import mapping as mapping_mod
from integrations.paperclip.client import FixtureTransport, HttpTransport, PaperclipClient

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "diagrams.json"

COMPANY = "acme"
PATH = f"/api/companies/{COMPANY}/diagrams"


def _response(payload, *, status=200, path=PATH):
    return {"responses": [{"method": "GET", "path": path, "status": status, "body": payload}]}


def _payload(findings=None, *, content_hash="sha256:abc", rendered="docs/diagrams/live.svg"):
    blueprint = {"content_hash": content_hash}
    if rendered is not None:
        blueprint["rendered"] = rendered
    return {"blueprint": blueprint, "findings": findings or []}


def _client(fixture):
    return PaperclipClient(company_id=COMPANY, transport=FixtureTransport(fixture))


def _receipts(report):
    return [item["receipt"] for item in report.evidence]


def _receipt_for(report, ref):
    matches = [r for r in _receipts(report) if r["ref"] == ref]
    assert len(matches) == 1, f"expected exactly one receipt for {ref!r}, got {matches}"
    return matches[0]


def _sha(report):
    return hashlib.sha256(diagrams_mod.render(report).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# The shape
# --------------------------------------------------------------------------


def test_projects_the_frozen_evidence_receipt_shape():
    report = diagrams_mod.project(_client(FIXTURE), company_id=COMPANY)
    assert report.exit_code() == 0, report.findings
    assert report.source["adr"] == "ADR-0017"
    assert report.source["authority"] == "none"
    assert report.source["read_only"] is True
    # every emitted receipt is exactly the frozen v2 receipt
    for receipt in _receipts(report):
        assert set(receipt) == {"kind", "ref", "result", "checks"}
        assert receipt["result"] in diagrams_mod.RECEIPT_RESULTS
    kinds = {r["kind"] for r in _receipts(report)}
    assert kinds == {
        diagrams_mod.RECEIPT_KIND_BLUEPRINT,
        diagrams_mod.RECEIPT_KIND_RENDER,
        diagrams_mod.RECEIPT_KIND_DRIFT,
    }
    # the drift Finding is a FAIL receipt; the aligned one is PASS (not drift)
    assert _receipt_for(report, "gateway/proxy/router.py")["result"] == "FAIL"
    assert _receipt_for(report, "engine/core/state.py")["result"] == "PASS"
    # the rendered path rides its own receipt, the content_hash its own
    assert _receipt_for(report, "docs/diagrams/live-architecture.svg")["kind"] == (
        diagrams_mod.RECEIPT_KIND_RENDER
    )
    assert _receipt_for(report, "sha256:6b1f0c8a41d5e2f7b9c0a3d64e8f1b2c5d7a9e0f3b6c8d1e4f7a0b3c6d9e2f5a")[
        "kind"
    ] == diagrams_mod.RECEIPT_KIND_BLUEPRINT


def test_receipts_conform_to_the_v2_evidence_receipt_schema():
    report = diagrams_mod.project(_client(FIXTURE), company_id=COMPANY)
    schema = diagrams_mod.receipt_schema(ROOT)
    for receipt in _receipts(report):
        assert mapping_mod.validate(receipt, schema, "receipt") == []


def test_emits_no_facet_and_no_new_ticket_kind():
    report = diagrams_mod.project(_client(FIXTURE), company_id=COMPANY)
    document = diagrams_mod.to_dict(report)
    assert set(document) == {"diagrams", "evidence", "findings"}
    assert "facets" not in json.dumps(document)
    ticket = mapping_mod.load_schema(ROOT, "ticket")
    # the closed facet set is untouched: no diagrams member (ADR-0017)
    assert set(ticket["properties"]["facets"]["properties"]) == {
        "lessons",
        "raid",
        "budget",
    }
    # no new ticket kind: the closed vocabulary is unchanged (ADR-0014)
    assert set(ticket["properties"]["kind"]["enum"]) == {
        "task",
        "incident",
        "rca",
        "corrective-action",
        "lesson",
        "suggestion",
    }


def test_reuses_the_seam_transport_and_records_the_request_shape():
    transport = FixtureTransport(FIXTURE)
    client = PaperclipClient(company_id=COMPANY, transport=transport)
    diagrams_mod.project(client, company_id=COMPANY)
    assert transport.requests == [
        {
            "method": "GET",
            "path": PATH,
            "headers": {"Accept": "application/json"},
        }
    ]
    # the transport behind the client is the seam's, not a second implementation
    assert diagrams_mod.transport_of(client) is transport


def test_accepts_a_bare_transport_not_just_a_client():
    report = diagrams_mod.project(FixtureTransport(FIXTURE), company_id=COMPANY)
    assert report.exit_code() == 0, report.findings


def test_http_transport_is_only_on_the_live_path():
    # Offline by construction: the projection reads whatever transport the seam
    # client carries, and every test here carries a FixtureTransport. The live
    # transport is exposed by the seam but is never built by this suite or the
    # gate, so no test touches the network.
    client = _client(FIXTURE)
    assert isinstance(client.transport, FixtureTransport)
    assert diagrams_mod.transport_of(client) is client.transport
    assert HttpTransport is not None
    assert diagrams_mod.project(client, company_id=COMPANY).exit_code() == 0


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_projection_is_deterministic():
    first = _sha(diagrams_mod.project(_client(FIXTURE), company_id=COMPANY))
    second = _sha(diagrams_mod.project(_client(FIXTURE), company_id=COMPANY))
    assert first == second


def test_sha_is_unchanged_by_a_negative_control_run():
    before = _sha(diagrams_mod.project(_client(FIXTURE), company_id=COMPANY))
    # a refusal run over a *different* fixture must not perturb the plan
    refused = diagrams_mod.project(
        _client(_response(_payload([{"attribute": "replicas"}]))), company_id=COMPANY
    )
    assert refused.exit_code() == 1
    after = _sha(diagrams_mod.project(_client(FIXTURE), company_id=COMPANY))
    assert before == after


# --------------------------------------------------------------------------
# Negative controls (each refused by name)
# --------------------------------------------------------------------------


def test_an_empty_fixture_is_cannot_assess():
    report = diagrams_mod.project(_client({"responses": []}), company_id=COMPANY)
    assert report.exit_code() == 2
    assert any("field: fixture" in reason for reason in report.cannot_assess)


def test_a_missing_fixture_path_is_cannot_assess():
    report = diagrams_mod.project_fixture(
        "/tmp/ao465-does-not-exist.json", company_id=COMPANY
    )
    assert report.exit_code() == 2
    assert any("field: fixture" in reason for reason in report.cannot_assess)


def test_a_fixture_without_a_responses_key_is_cannot_assess():
    report = diagrams_mod.project_fixture({"note": "empty"}, company_id=COMPANY)
    assert report.exit_code() == 2
    assert any("field: fixture" in reason for reason in report.cannot_assess)


def test_a_finding_with_no_resource_id_is_refused():
    report = diagrams_mod.project(
        _client(_response(_payload([{"attribute": "replicas", "declared": 3, "live": 1}]))),
        company_id=COMPANY,
    )
    assert report.exit_code() == 1
    assert any("field: resource" in finding for finding in report.findings)
    assert all(r["kind"] != diagrams_mod.RECEIPT_KIND_DRIFT for r in _receipts(report))


def test_equal_attributes_are_not_reported_as_drift():
    report = diagrams_mod.project(
        _client(
            _response(
                _payload(
                    [
                        {
                            "resource": "engine/core/state.py",
                            "attribute": "timeout",
                            "declared": "30s",
                            "live": "30s",
                            "status": "aligned",
                        }
                    ]
                )
            )
        ),
        company_id=COMPANY,
    )
    assert report.exit_code() == 0, report.findings
    receipt = _receipt_for(report, "engine/core/state.py")
    assert receipt["result"] == "PASS"
    # no receipt in the whole projection reports this resource as drift
    assert not any(
        r["result"] == "FAIL" and r["ref"] == "engine/core/state.py" for r in _receipts(report)
    )
    # the blueprint carries no drift either
    assert all(r["result"] == "PASS" for r in _receipts(report))


def test_a_false_positive_drift_is_refused():
    report = diagrams_mod.project(
        _client(
            _response(
                _payload(
                    [
                        {
                            "resource": "engine/core/state.py",
                            "attribute": "timeout",
                            "declared": "30s",
                            "live": "30s",
                            "status": "drift",
                        }
                    ]
                )
            )
        ),
        company_id=COMPANY,
    )
    assert report.exit_code() == 1
    assert any(
        "field: status" in finding and "lying signal" in finding
        for finding in report.findings
    )
    assert not any(r["kind"] == diagrams_mod.RECEIPT_KIND_DRIFT for r in _receipts(report))


def test_a_status_outside_the_closed_vocabulary_is_refused():
    report = diagrams_mod.project(
        _client(
            _response(
                _payload(
                    [
                        {
                            "resource": "gateway/proxy/router.py",
                            "declared": "3",
                            "live": "1",
                            "status": "frobnicated",
                        }
                    ]
                )
            )
        ),
        company_id=COMPANY,
    )
    assert report.exit_code() == 1
    assert any("field: status" in finding for finding in report.findings)


def test_a_forbidden_response_is_cannot_assess():
    report = diagrams_mod.project(
        _client(_response({"error": "forbidden"}, status=403)), company_id=COMPANY
    )
    assert report.exit_code() == 2
    assert any("field: http-status" in reason for reason in report.cannot_assess)
    assert any("403" in reason for reason in report.cannot_assess)


def test_a_not_found_response_is_cannot_assess():
    report = diagrams_mod.project(
        _client(_response({"error": "not found"}, status=404)), company_id=COMPANY
    )
    assert report.exit_code() == 2
    assert any("field: http-status" in reason for reason in report.cannot_assess)
    assert any("404" in reason for reason in report.cannot_assess)


def test_a_missing_company_scope_is_cannot_assess():
    report = diagrams_mod.project(FixtureTransport(FIXTURE), company_id="")
    assert report.exit_code() == 2
    assert any("field: company_id" in reason for reason in report.cannot_assess)


def test_a_blueprint_with_no_content_hash_is_refused():
    report = diagrams_mod.project(
        _client(_response(_payload(content_hash=""))), company_id=COMPANY
    )
    assert report.exit_code() == 1
    assert any("field: content_hash" in finding for finding in report.findings)
