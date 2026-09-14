"""Per-task budget scope adapter tests (issue #415).

Every test drives the adapter with a controlled tree in ``tmp_path`` so the
derivation is exercised deterministically and offline: the committed rail is
copied in, the metering store and ticket store are synthesized. The tests prove
the four seam mismatches (#7–#10) are closed and that each refusal really
refuses, so the gate's expectations are not a formality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.paperclip import budget as budget_mod
from integrations.paperclip import mapping as mapping_mod

ROOT = Path(__file__).resolve().parents[3]

RAIL_FILES = budget_mod.RAIL_RELPATHS


def _tree(tmp_path: Path) -> Path:
    """A scratch rail + metering + ticket tree rooted at ``tmp_path``."""
    for rel in RAIL_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / rel).read_bytes())
    metering = tmp_path / budget_mod.METERING_RELPATH
    metering.parent.mkdir(parents=True, exist_ok=True)
    metering.write_text(
        "\n".join(
            [
                json.dumps({"kind": "usage", "billable": True, "tenantId": "acme",
                            "agentId": "coder", "costUsd": 30.0}),
                json.dumps({"kind": "usage", "billable": True, "tenantId": "acme",
                            "agentId": "coder", "costUsd": 10.0}),
                json.dumps({"kind": "usage", "billable": False, "tenantId": "acme",
                            "agentId": "reader", "costUsd": 999.0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    tickets = tmp_path / budget_mod.TICKETS_RELPATH
    tickets.parent.mkdir(parents=True, exist_ok=True)
    tickets.write_text(
        json.dumps(
            {
                "tickets": [
                    {
                        "id": "kushin77/agent-orchestrator#415",
                        "evidence": [
                            {"kind": "gate-run", "ref": "deadbeef", "result": "PASS", "checks": 30}
                        ],
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _write_charges(tmp_path: Path, rows) -> Path:
    path = tmp_path / budget_mod.CHARGE_LEDGER_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _charge(**overrides):
    row = {
        "ticket": "kushin77/agent-orchestrator#415",
        "receipt": "deadbeef",
        "scope": {"level": "team", "id": "acme"},
        "cap": 120.0,
        "currency": "USD",
        "hard_cap_pct": 100,
        "warn_at_pct": 80,
    }
    row.update(overrides)
    return row


def test_scope_is_derived_from_the_rail_producers(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(
        tree,
        [
            _charge(),
            _charge(scope={"level": "agent", "id": "coder"}, cap=50.0),
        ],
    )
    report = budget_mod.build(tree)
    assert report.exit_code() == 0, report.findings
    scopes = sorted((r["scope"]["level"], r["scope"]["id"]) for r in report.records)
    assert scopes == [("agent", "coder"), ("team", "acme")]


def test_spend_comes_from_the_metering_store_and_ignores_non_billable(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(scope={"level": "agent", "id": "coder"}, cap=50.0)])
    report = budget_mod.build(tree)
    assert report.exit_code() == 0, report.findings
    # 30 + 10 billable; the non-billable 999 must not count.
    assert report.records[0]["spent"] == 40.0


def test_currency_is_stated_not_assumed(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(currency="EUR")])
    report = budget_mod.build(tree)
    assert report.exit_code() == 0, report.findings
    assert report.records[0]["currency"] == "EUR"
    assert report.currency_source["currency"] == "charge"

    tree2 = _tree(tmp_path / "default")
    _write_charges(tree2, [_charge(currency=None)])
    default_report = budget_mod.build(tree2)
    assert default_report.exit_code() == 0, default_report.findings
    assert default_report.records[0]["currency"] == budget_mod.DEFAULT_CURRENCY
    assert default_report.currency_source["currency"] == "fleet-default"


def test_build_is_deterministic(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(), _charge(scope={"level": "agent", "id": "coder"}, cap=50.0)])
    first = budget_mod.build(tree)
    second = budget_mod.build(tree)
    assert [r for r in first.records] == [r for r in second.records]


def test_records_conform_to_the_seam_budget_schema(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(), _charge(scope={"level": "agent", "id": "coder"}, cap=50.0)])
    report = budget_mod.build(tree)
    schema = mapping_mod.load_schema(ROOT, "budget")
    for record in report.records:
        payload = {k: v for k, v in record.items() if k != "_ticket"}
        assert mapping_mod.validate(payload, schema, "budget") == []


def test_facet_mirrors_the_closed_ticket_shape(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge()])
    record = budget_mod.build(tree).records[0]
    facet = budget_mod.to_facet(record, ticket="kushin77/agent-orchestrator#415")
    assert set(facet) == {"scope", "spent", "cap", "receipt"}
    assert facet["receipt"] == record["receipt_ref"]


def test_receipt_with_no_ticket_is_refused(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(receipt="ghost-receipt")])
    report = budget_mod.build(tree)
    assert report.exit_code() == 1
    assert any("ghost-receipt" in f for f in report.findings)
    assert report.records == ()


def test_cap_with_no_scope_level_is_refused(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(scope={"id": "acme"})])
    report = budget_mod.build(tree)
    assert report.exit_code() == 1
    assert any("no scope level" in f for f in report.findings)


def test_a_level_with_no_producer_fails_closed(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(scope={"level": "project", "id": "m27"})])
    report = budget_mod.build(tree)
    assert report.exit_code() == 1
    assert any("no producer" in f for f in report.findings)


def test_agent_level_without_metering_attribution_fails_closed(tmp_path):
    tree = _tree(tmp_path)
    metering = tree / budget_mod.METERING_RELPATH
    metering.write_text(
        json.dumps({"kind": "usage", "billable": True, "tenantId": "acme", "costUsd": 1.0}) + "\n",
        encoding="utf-8",
    )
    _write_charges(tree, [_charge(scope={"level": "agent", "id": "coder"}, cap=50.0)])
    report = budget_mod.build(tree)
    assert report.exit_code() == 1
    assert any("no producer" in f for f in report.findings)


def test_currency_less_row_is_refused(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(currency="")])
    report = budget_mod.build(tree)
    assert report.exit_code() == 1
    assert any("currency-less row" in f for f in report.findings)


def test_a_spend_with_no_declared_cap_is_refused(tmp_path):
    tree = _tree(tmp_path)
    _write_charges(tree, [_charge(scope={"level": "agent", "id": "coder"}, cap=None)])
    report = budget_mod.build(tree)
    assert report.exit_code() == 1
    assert any("no declared cap" in f for f in report.findings)


def test_a_missing_rail_is_cannot_assess_never_a_pass(tmp_path):
    tree = _tree(tmp_path)
    for rel in RAIL_FILES:
        (tree / rel).unlink()
    _write_charges(tree, [_charge()])
    report = budget_mod.build(tree)
    assert report.exit_code() == 2
    assert report.records == ()


def test_a_missing_metering_store_is_cannot_assess(tmp_path):
    tree = _tree(tmp_path)
    (tree / budget_mod.METERING_RELPATH).unlink()
    _write_charges(tree, [_charge()])
    report = budget_mod.build(tree)
    assert report.exit_code() == 2


def test_a_missing_ticket_store_is_cannot_assess(tmp_path):
    tree = _tree(tmp_path)
    (tree / budget_mod.TICKETS_RELPATH).unlink()
    _write_charges(tree, [_charge()])
    report = budget_mod.build(tree)
    assert report.exit_code() == 2


def test_no_charge_ledger_is_not_a_failure(tmp_path):
    tree = _tree(tmp_path)
    report = budget_mod.build(tree)
    assert report.exit_code() == 0
    assert report.records == ()


def test_hard_stop_is_the_boolean_form_of_the_percentage_rail():
    assert budget_mod.derive_hard_stop(100) is True
    assert budget_mod.derive_hard_stop(100, soft=True) is False


def test_hand_off_stops_and_asks_at_the_cap():
    at_cap = {
        "scope": {"level": "team", "id": "acme"},
        "period": "month",
        "cap": 120.0,
        "spent": 120.0,
        "currency": "USD",
        "hard_stop": True,
        "burn_rate_alert_pct": 80.0,
        "receipt_ref": "deadbeef",
    }
    stop = budget_mod.hand_off(at_cap)
    assert stop["action"] == budget_mod.ACTION_STOP_HANDOFF
    assert "stops and asks" in stop["reason"]
    below = budget_mod.hand_off(dict(at_cap, spent=10.0))
    assert below["action"] == budget_mod.ACTION_OK
    warn = budget_mod.hand_off(dict(at_cap, spent=100.0))
    assert warn["action"] == budget_mod.ACTION_WARN


def test_hand_off_refuses_a_cap_less_spend():
    decision = budget_mod.hand_off({"cap": 0, "spent": 5.0, "currency": "USD"})
    assert decision["action"] == budget_mod.ACTION_STOP_HANDOFF


@pytest.mark.parametrize(
    "raw,expected",
    [(80, 0.8), (0.8, 0.8), (None, budget_mod.DEFAULT_WARN_AT_PCT)],
)
def test_fraction_normalizes_percent_and_ratio(raw, expected):
    assert budget_mod._fraction(raw, budget_mod.DEFAULT_WARN_AT_PCT) == pytest.approx(expected)


def test_burn_rate_alert_is_derived_from_warn_over_hard_cap():
    charge = budget_mod.Charge(
        ticket="kushin77/agent-orchestrator#415",
        receipt="deadbeef",
        scope_level="team",
        scope_id="acme",
        cap=120.0,
        spent=0.0,
        currency="USD",
        hard_cap_pct=100.0,
        warn_at_pct=80.0,
        where="row",
    )
    rail = budget_mod.Rail(
        policies={
            "acme": budget_mod.RailPolicy(
                scope_id="acme", producer="rail", cap=120.0, warn_at_pct=80.0, hard_cap_pct=100.0
            )
        },
        present=True,
    )
    record, findings, _ = budget_mod.derive_charge(
        charge,
        rail=rail,
        producers={"agent": set(), "team": {"acme"}, "project": set()},
        tickets={"#415": {"deadbeef"}},
        metering=budget_mod.Metering(present=True),
    )
    assert findings == []
    assert record is not None
    assert record["burn_rate_alert_pct"] == 80.0
    assert record["hard_stop"] is True
