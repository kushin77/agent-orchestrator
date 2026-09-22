"""Deterministic fixture trees for the approvals adapter (issue #416).

One builder writes a small, complete tree exercising a grant, a deny and a
pending for the three kinds; the gate, the negative controls and the pytest
suite all build from it, so the fixture is the single description of the shape
the projector reads. Nothing here writes to the repository — the trees are built
under a caller-supplied root (a tmpdir).

---knowledge---
module_id: integrations.paperclip.adapters.approvals.fixtures
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [build_tree, write_record, remove_record]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

FIXTURE_FILES: Dict[str, Dict[str, Any]] = {
    # --- requests (operator -> brain); a request carries no decision ---------
    ".fleet/brain/inbox/req-topup.json": {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "id": "order-topup-1",
        "ts": "2026-09-14T00:00:00Z",
        "task": {"issue": 416},
        "body": "request a budget top-up for the paperclip agent",
        "approval": {
            "kind": "top-up",
            "subject": "budget:agent/paperclip",
            "requested_by": "operator",
            "reason": "adoption burn",
        },
    },
    ".fleet/brain/inbox/req-override.json": {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "id": "order-override-1",
        "ts": "2026-09-14T00:00:01Z",
        "task": {"issue": 999, "override": True},
        "body": "request an override of the live claim on #999",
        "approval": {"kind": "override", "subject": "issue:999", "requested_by": "operator"},
    },
    # A request the fleet never decided: it must project as `pending`.
    ".fleet/brain/inbox/req-pending.json": {
        "from": "operator",
        "to": "brain",
        "type": "directive",
        "id": "order-topup-2",
        "ts": "2026-09-14T00:00:02Z",
        "task": {"issue": 417},
        "body": "request a top-up that the brain has not decided",
        "approval": {"kind": "top-up", "subject": "budget:agent/secrets", "requested_by": "operator"},
    },
    # --- decisions (the authority) ------------------------------------------
    ".board/claims/claim-416.json": {
        "event": "claim",
        "issue": 416,
        "agent": "ao-session-416",
        "at": "2026-09-14T00:01:00Z",
        "lane": "approvals",
        "base_commit": "18124b2",
        "reason": "next-in-milestone",
        "ttl_hours": 4,
    },
    ".board/claims/reap-555.json": {
        "event": "reap",
        "issue": 555,
        "agent": "ao-session-999",
        "at": "2026-09-14T00:02:00Z",
        "lane": "override",
        "reason": "brain-directed",
        "ttl_hours": 4,
        "reaped_agent": "ao-stale",
    },
    ".fleet/sent/directive-topup.json": {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "id": "brain-directive-topup1",
        "nonce": "brain-nonce-topup1",
        "ts": "2026-09-14T00:03:00Z",
        "task": {"issue": 416},
        "body": "grant the requested top-up",
        "approval": {
            "kind": "top-up",
            "subject": "budget:agent/paperclip",
            "decision": "grant",
            "actor": "brain",
        },
    },
    ".fleet/sent/directive-override.json": {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "id": "brain-directive-override1",
        "nonce": "brain-nonce-override1",
        "ts": "2026-09-14T00:04:00Z",
        "task": {"issue": 999, "override": True},
        "control": "override",
        "body": "operator override: dispatch #999 now",
        "approval": {"kind": "override", "subject": "issue:999"},
    },
}


def build_tree(root: Path | str) -> Path:
    """Write the fixture tree under ``root``; return the root."""
    root = Path(root)
    for relative, payload in FIXTURE_FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return root


def write_record(root: Path | str, relative: str, payload: Dict[str, Any]) -> Path:
    """Write one extra record (used by the negative controls to provoke a refusal)."""
    target = Path(root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def remove_record(root: Path | str, relative: str) -> None:
    """Delete one fixture record (used to prove a projection with no record fails)."""
    (Path(root) / relative).unlink()
