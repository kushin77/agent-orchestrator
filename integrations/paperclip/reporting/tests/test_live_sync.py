"""Live sync: auth-gated pull + schema validation + append-only audit (#888).

Negative controls (a bad input refused by name), all offline: no test in this
module calls a live network — tickets/budgets replay through
``FixtureTransport``, heartbeats replay through a canned reader, and the
signing key is an obviously-fake placeholder.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.paperclip import client as client_mod
from integrations.paperclip.auth import registry as registry_mod
from integrations.paperclip.reporting.sync import live as live_mod

NOW = 1_700_000_000
COMPANY = "acme"
SECRET = "placeholder-signing-key-not-a-real-credential"
FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "api.json"


def _client() -> client_mod.PaperclipClient:
    transport = client_mod.FixtureTransport(FIXTURE, token="tok-123", run_id="run-1")
    return client_mod.PaperclipClient(company_id=COMPANY, transport=transport)


def _agent_headers() -> dict:
    token = registry_mod.mint_agent_key(
        ROOT, "paperclip", company=COMPANY, secret=SECRET, now=NOW
    )
    return {"Authorization": f"Bearer {token}"}


def _fake_heartbeat_reader(root, rung, *, session_id, now):
    return {
        "agent_id": rung,
        "session_id": session_id,
        "tick": 0,
        "cadence_seconds": 300,
        "wake": {"cause": "poll", "delta": {}},
        "outcome": {"status": "idle"},
        "ts": now,
    }


class _FakeReport:
    def __init__(self, records):
        self.records = records


def _fake_budget_builder(root):
    return _FakeReport(
        [
            {
                "receipt_ref": "kushin77/agent-orchestrator#888",
                "scope": {"level": "agent", "id": "paperclip"},
                "amount": 1.5,
            }
        ]
    )


def _run(tmp_path, **overrides):
    kwargs = dict(
        headers=_agent_headers(),
        root=ROOT,
        company=COMPANY,
        secret=SECRET,
        now=NOW,
        client=_client(),
        heartbeat_reader=_fake_heartbeat_reader,
        budget_builder=_fake_budget_builder,
        audit_trail=tmp_path / "sync-audit.jsonl",
    )
    kwargs.update(overrides)
    return live_mod.run_sync(**kwargs)


def test_schema_id_is_frozen():
    schema = live_mod.load_schema()
    assert schema["$id"] == live_mod.SCHEMA_ID


def test_successful_sync_pulls_all_three_and_audits(tmp_path):
    record = _run(tmp_path)
    assert record.principal == "agent:paperclip"
    assert record.tickets == []  # the fixture's acme/issues is an empty list
    assert len(record.heartbeats) == len(live_mod.DEFAULT_RUNGS)
    assert len(record.budgets) == 1

    trail = tmp_path / "sync-audit.jsonl"
    lines = trail.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema"] == live_mod.SCHEMA_ID
    assert payload["counts"] == {"tickets": 0, "heartbeats": 3, "budgets": 1}


def test_sync_is_append_only_across_two_runs(tmp_path):
    trail = tmp_path / "sync-audit.jsonl"
    _run(tmp_path, audit_trail=trail)
    _run(tmp_path, audit_trail=trail)
    lines = trail.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# -- negative controls: refused by name, nothing written to the trail --------


def test_missing_authorization_is_refused_by_name(tmp_path):
    trail = tmp_path / "sync-audit.jsonl"
    with pytest.raises(live_mod.SyncRefusal) as exc:
        _run(tmp_path, headers={}, audit_trail=trail)
    assert exc.value.code == live_mod.CODE_UNAUTHENTICATED
    assert not trail.exists()


def test_invalid_token_is_refused_by_name(tmp_path):
    trail = tmp_path / "sync-audit.jsonl"
    with pytest.raises(live_mod.SyncRefusal) as exc:
        _run(tmp_path, headers={"Authorization": "Basic bm90LWEtdG9rZW4="}, audit_trail=trail)
    assert exc.value.code == live_mod.CODE_UNAUTHENTICATED
    assert not trail.exists()


def test_permission_the_caller_lacks_is_refused_by_name(tmp_path):
    trail = tmp_path / "sync-audit.jsonl"
    with pytest.raises(live_mod.SyncRefusal) as exc:
        _run(tmp_path, permission="agent:mint", audit_trail=trail)
    assert exc.value.code == live_mod.CODE_FORBIDDEN
    assert not trail.exists()


class _MalformedTicketsClient:
    """A client whose `issues()` returns a ticket that is not an object."""

    def issues(self):
        from integrations.paperclip.model import Response

        return Response(status=200, body={"issues": ["not-an-object"]})


def test_invalid_payload_is_refused_and_never_audited(tmp_path):
    trail = tmp_path / "sync-audit.jsonl"

    with pytest.raises(live_mod.SyncRefusal) as exc:
        _run(tmp_path, client=_MalformedTicketsClient(), audit_trail=trail)
    assert exc.value.code == live_mod.CODE_PAYLOAD_INVALID
    assert not trail.exists()


def test_frozen_schema_with_wrong_id_is_cannot_assess(tmp_path):
    doctored = tmp_path / "schema-dir"
    doctored.mkdir()
    (doctored / live_mod.SCHEMA_FILE).write_text(
        json.dumps({"$id": "not-the-frozen-schema"}), encoding="utf-8"
    )
    with pytest.raises(live_mod.SyncRefusal) as exc:
        _run(tmp_path, schema_directory=doctored)
    assert exc.value.code == live_mod.CODE_SCHEMA_FROZEN
