"""paperclip sync adapter tests (issue #1649): offline, no network.

Proves the two Done-line requirements: (1) a round trip — plan -> tickets ->
plan — comes back with the same ticket ids/goal/blocked_by; (2) the adapter
refuses by name when ``enable_paperclip`` is off (the default).
"""

from __future__ import annotations

from typing import Any

import pytest

from integrations.paperclip.adapters.sync import adapter
from integrations.paperclip.model import Response


class FakePaperclipClient:
    """Offline stand-in for ``integrations.paperclip.client.PaperclipClient``.

    Stores created tickets in memory and echoes them back from ``issues()``,
    same shape a real Paperclip company endpoint would: ``{status, body}``.
    """

    def __init__(self) -> None:
        self._tickets: dict[str, dict[str, Any]] = {}

    def create_issue(self, payload: dict[str, Any]) -> Response:
        record = dict(payload)
        self._tickets[record["id"]] = record
        return Response(status=201, body=record)

    def issues(self) -> Response:
        return Response(status=200, body=list(self._tickets.values()))


def _plan_document() -> dict[str, Any]:
    return {
        "goal": "CRM + Asterisk voice fully operational",
        "milestones": [
            {
                "id": "M1",
                "name": "Foundations",
                "order": 0,
                "exit_criteria": [{"description": "gate green", "command": "make verify", "expect": "PASS"}],
            }
        ],
        "tasks": [
            {
                "id": "T1",
                "repo": "agent-orchestrator",
                "issue": 1649,
                "module": "integrations/paperclip",
                "milestone": "M1",
                "priority": 1,
                "depends_on": [],
                "sme": "integrations-sme",
                "tier": "L1",
                "status_source": "github",
            },
            {
                "id": "T2",
                "repo": "agent-orchestrator",
                "issue": 1650,
                "module": "integrations/paperclip",
                "milestone": "M1",
                "priority": 2,
                "depends_on": ["T1"],
                "sme": "integrations-sme",
                "tier": "L1",
                "status_source": "github",
            },
        ],
    }


def test_push_then_pull_round_trips_the_plan_tickets():
    document = _plan_document()
    client = FakePaperclipClient()

    pushed = adapter.push_plan(client, document, enabled=True)
    assert {t["id"] for t in pushed} == {"kushin77/agent-orchestrator#1649", "kushin77/agent-orchestrator#1650"}

    pulled = adapter.pull_plan(client, enabled=True)
    pulled_by_id = {t["id"]: t for t in pulled}

    assert pulled_by_id["kushin77/agent-orchestrator#1649"]["goal"] == "M1"
    assert pulled_by_id["kushin77/agent-orchestrator#1649"]["blocked_by"] == []
    assert pulled_by_id["kushin77/agent-orchestrator#1650"]["blocked_by"] == [
        "kushin77/agent-orchestrator#1649"
    ]
    assert pulled_by_id["kushin77/agent-orchestrator#1650"]["kind"] == "task"


def test_push_refused_by_name_when_flag_off():
    client = FakePaperclipClient()
    with pytest.raises(adapter.SyncRefused, match="enable_paperclip"):
        adapter.push_plan(client, _plan_document(), enabled=False)
    assert client._tickets == {}  # the client was never called


def test_pull_refused_by_name_when_flag_off():
    client = FakePaperclipClient()
    with pytest.raises(adapter.SyncRefused, match="enable_paperclip"):
        adapter.pull_plan(client, enabled=False)


def test_flag_reads_off_from_the_real_registry_by_default():
    # No override: reads infra/feature-flags/registry.yaml directly, which
    # declares enable_paperclip default off (issue #411/#1649) — the adapter
    # must never turn it on.
    assert adapter.flags.paperclip_enabled() is False
