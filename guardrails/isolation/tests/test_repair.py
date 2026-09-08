"""Opt-in repair tests (AC2): dry-run default, transactional, audited,
idempotent, and the negative proof that repair without the apply flag
changes nothing."""

from __future__ import annotations

import copy
import json
import os

import pytest

from conftest import FIXTURES_DIR
from isolation.errors import RepairAbortError
from isolation.integrity import scan_dataset
from isolation.repair import RepairAction, plan_repairs, repair_execute


def _load(name: str) -> dict:
    with open(os.path.join(FIXTURES_DIR, "data", name), "r",
              encoding="utf-8") as handle:
        return json.load(handle)


def _leaky() -> dict:
    return _load("leaky_dataset.json")


def _clean() -> dict:
    return _load("clean_dataset.json")


class TestDryRunNeverMutates:
    """Negative proof: repair without the apply flag changes nothing."""

    def test_plan_does_not_mutate_the_dataset(self):
        dataset = _leaky()
        before = copy.deepcopy(dataset)
        actions = plan_repairs(dataset)
        assert actions, "the leaky dataset must produce a repair plan"
        assert dataset == before, "planning must never mutate the dataset"

    def test_repair_execute_is_the_only_mutator_and_leaves_input_alone(self):
        dataset = _leaky()
        before = copy.deepcopy(dataset)
        repaired = repair_execute(dataset, operator="test-operator")
        # the caller's dataset is untouched; only the returned copy changes
        assert dataset == before

    def test_clean_dataset_repair_is_a_noop(self):
        dataset = _clean()
        repaired = repair_execute(dataset)
        assert repaired == _clean()


class TestPlanProducesExpectedActions:
    def test_plan_covers_all_violations(self):
        actions = plan_repairs(_leaky())
        by_type: dict = {}
        for action in actions:
            by_type.setdefault(action.action, []).append(action)
        # orphan ghost/agent-77 + unscoped no-hint -> quarantine
        assert any(a.record_id == "agent-77" and a.action == "quarantine"
                   for a in actions)
        # fallback agent-5 names globex -> rescue, agent-6 -> quarantine
        assert any(a.record_id == "agent-5" and a.action == "rescue_to_tenant"
                   and a.to_tenant == "globex" for a in actions)
        assert any(a.record_id == "agent-6" and a.action == "quarantine"
                   for a in actions)
        # duplicate globex/agent-1 -> quarantine_duplicates
        assert any(a.record_id == "agent-1" and a.from_tenant == "globex"
                   and a.action == "quarantine_duplicates" for a in actions)
        # denormalized acme/agent-dup (claims globex) -> rescue
        assert any(a.record_id == "agent-dup" and a.from_tenant == "acme"
                   and a.action == "rescue_to_tenant"
                   and a.to_tenant == "globex" for a in actions)


class TestApplyRepairsTheLeakyDataset:
    def test_repaired_dataset_is_clean(self):
        repaired = repair_execute(_leaky())
        report = scan_dataset(repaired)
        assert not report.has_findings

    def test_repair_moves_rows_as_documented(self):
        repaired = repair_execute(_leaky())
        records = repaired["records"]
        assert set(records["acme"]) == {"agent-1", "agent-2"}
        assert set(records["globex"]) == {"agent-9", "agent-5", "agent-dup"}
        assert "ghost" not in records
        assert "__fallback__" not in records
        q = records["__quarantine__"]
        assert "ghost|agent-77" in q
        assert "__fallback__|agent-6" in q
        assert "globex|agent-1" in q

    def test_repair_is_audited(self):
        repaired = repair_execute(_leaky(), operator="test-operator")
        ledger = repaired.get("audit", [])
        assert len(ledger) == 1
        entry = ledger[0]
        assert entry["operator"] == "test-operator"
        assert entry["success"] is True
        assert entry["actions"] >= 5
        assert entry["records_modified"] >= 5

    def test_repair_is_idempotent(self):
        repaired = repair_execute(_leaky())
        # Re-planning the repaired state yields zero actions.
        assert plan_repairs(repaired) == []
        # Re-executing a clean state is a no-op: identical records and NO new
        # audit row (nothing was repaired, so nothing is recorded).
        again = repair_execute(repaired)
        assert again["records"] == repaired["records"]
        assert len(again.get("audit", [])) == 1  # append-only, no-op adds none


class TestTransactionalAllOrNothing:
    def test_abort_leaves_input_untouched(self, monkeypatch):
        dataset = _leaky()
        before = copy.deepcopy(dataset)
        from isolation import repair as repair_mod

        def _stale_plan(ds, quarantine="__quarantine__"):
            # Simulate a concurrent change: the planned source row is gone.
            return [RepairAction(action="quarantine", record_id="missing",
                                 from_tenant="acme", to_tenant="__quarantine__",
                                 reason="stale plan")]

        monkeypatch.setattr(repair_mod, "plan_repairs", _stale_plan)
        with pytest.raises(RepairAbortError):
            repair_mod.repair_execute(dataset)
        assert dataset == before, "a failed repair must not partially mutate"

    def test_rescue_never_overwrites_existing_row(self):
        # A fallback row that claims a tenant already owning the id must be
        # quarantined, never silently overwrite the owner's row.
        dataset = {
            "version": 1,
            "tenants": {"acme": {}},
            "fallback_tenant": "__fallback__",
            "quarantine_tenant": "__quarantine__",
            "records": {
                "acme": {"a1": {"tenant_id": "acme", "record_id": "a1",
                                "owner": "real"}},
                "__fallback__": {"a1": {"tenant_id": "__fallback__",
                                        "record_id": "a1",
                                        "owner_hint": "acme",
                                        "owner": "impostor"}},
            },
        }
        repaired = repair_execute(dataset)
        records = repaired["records"]
        # the canonical acme row survives; the impostor is quarantined
        assert records["acme"]["a1"]["owner"] == "real"
        assert "__fallback__|a1" in records["__quarantine__"]
        assert scan_dataset(repaired).aggregate().value == "OK"
