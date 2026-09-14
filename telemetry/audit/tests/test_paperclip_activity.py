"""The audit -> Activity projection that makes the read model servable (#347).

The read model is served through the adopted paperclip surface (ADR-0013); this
suite proves the projection is deterministic, faithful and read-only over its
input.
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from integrations.paperclip import mapping  # noqa: E402  (after sys.path)


def test_activity_projection_is_deterministic(model):
    rows = model.filter(tenant="acme")
    first = json.dumps(mapping.map_audit_activities(rows), sort_keys=True)
    second = json.dumps(mapping.map_audit_activities(rows), sort_keys=True)
    assert first == second


def test_activity_projection_maps_every_stored_field(model):
    row = model.filter(action="policy.deny")[0]
    activity = mapping.activity_from_audit(row)
    assert activity.id == "acme:3"
    assert activity.actor == "agent:worker-1"
    assert activity.verb == "policy.deny"
    assert activity.object_ref == "gateway/proxy"
    assert activity.ts == "2026-09-08T10:02:00Z"
    assert activity.to_dict() == {
        "id": "acme:3",
        "actor": "agent:worker-1",
        "verb": "policy.deny",
        "object_ref": "gateway/proxy",
        "ts": "2026-09-08T10:02:00Z",
    }


def test_activity_projection_never_mutates_its_input(model):
    row = model.filter(action="policy.deny")[0]
    before = json.dumps(row, sort_keys=True)
    mapping.activity_from_audit(row)
    assert json.dumps(row, sort_keys=True) == before


def test_activity_projection_tolerates_a_missing_resource():
    activity = mapping.activity_from_audit(
        {
            "tenantId": "t",
            "seq": 7,
            "actor": "user:a",
            "action": "model.call",
            "ts": "2026-09-08T00:00:00Z",
        }
    )
    assert activity.id == "t:7"
    assert activity.object_ref == ""


def test_activity_projection_preserves_chain_order(model):
    rows = model.filter(tenant="acme")
    activities = mapping.map_audit_activities(rows)
    assert [activity["id"] for activity in activities] == [
        "acme:1",
        "acme:2",
        "acme:3",
        "acme:4",
    ]
