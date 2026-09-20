"""Tests for governance/pmo/plan.py (issue #1648).

Covers: schema validation of the committed plan.yaml, deterministic
rendering, and one provoked negative control per rc-1 rule (a rule that has
never been seen to fire is not a rule you can trust).
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plan as plan_module  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]


def test_committed_plan_loads_and_validates():
    document = plan_module.load_plan(str(ROOT))
    assert document["goal"]
    assert document["milestones"]
    assert document["tasks"]


def test_committed_plan_has_no_findings_offline():
    document = plan_module.load_plan(str(ROOT))
    findings = plan_module.check_plan(document, live=False)
    assert findings == []


def test_render_table_is_deterministic():
    document = plan_module.load_plan(str(ROOT))
    first = plan_module.render_table(document)
    second = plan_module.render_table(document)
    assert first == second


def test_render_paperclip_is_deterministic_and_shaped():
    document = plan_module.load_plan(str(ROOT))
    payload = plan_module.render_paperclip(document)
    assert payload == plan_module.render_paperclip(document)
    assert payload["project"]["id"] == "purebliss-crm-voice"
    assert payload["sync_adapter_status"] == "not-implemented"
    for ticket in payload["tickets"]:
        assert ticket["id"].startswith("kushin77/")
        assert ticket["kind"] == "task"


def test_no_stored_status_field_on_any_task():
    document = plan_module.load_plan(str(ROOT))
    for task in document["tasks"]:
        assert "status" not in task
        assert "state" not in task
        assert task["status_source"] == "github"


def _minimal_document():
    return {
        "goal": "test goal",
        "milestones": [
            {
                "id": "M0",
                "name": "first",
                "order": 0,
                "exit_criteria": [{"description": "x", "command": "true", "expect": "0"}],
            },
            {
                "id": "M1",
                "name": "second",
                "order": 1,
                "exit_criteria": [{"description": "y", "command": "true", "expect": "0"}],
            },
        ],
        "tasks": [
            {
                "id": "t-a",
                "repo": "agent-orchestrator",
                "issue": 1,
                "module": "x",
                "milestone": "M0",
                "priority": 1,
                "depends_on": [],
                "sme": "platform-sme",
                "tier": "L0",
                "status_source": "github",
            },
            {
                "id": "t-b",
                "repo": "agent-orchestrator",
                "issue": 2,
                "module": "x",
                "milestone": "M1",
                "priority": 1,
                "depends_on": ["t-a"],
                "sme": "platform-sme",
                "tier": "L0",
                "status_source": "github",
            },
        ],
    }


def test_negative_control_dependency_order_violation_unknown_dep():
    document = _minimal_document()
    document["tasks"][1]["depends_on"] = ["t-does-not-exist"]
    findings = plan_module.check_plan(document, live=False)
    rules = {f.rule for f in findings}
    assert "dependency-order-violation" in rules


def test_negative_control_closed_milestone_unmet(monkeypatch):
    document = _minimal_document()
    # t-a is in M0 (order 0); make it depend on t-b in M1 (order 1) and closed
    # -- a task must never close while depending on unmet work in a LATER
    # milestone.
    document["tasks"][0]["depends_on"] = ["t-b"]

    def fake_state(repo: str, issue: int) -> str | None:
        return "CLOSED" if issue == 1 else "OPEN"

    monkeypatch.setattr(plan_module, "_issue_state", fake_state)
    findings = plan_module.check_plan(document, live=True)
    rules = {f.rule for f in findings}
    assert "closed-milestone-unmet" in rules


def test_negative_control_missing_issue(monkeypatch):
    document = _minimal_document()

    def fake_state(repo: str, issue: int) -> str | None:
        return None

    monkeypatch.setattr(plan_module, "_issue_state", fake_state)
    findings = plan_module.check_plan(document, live=True)
    rules = {f.rule for f in findings}
    assert "missing-issue" in rules
