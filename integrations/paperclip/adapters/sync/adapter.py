"""paperclip.adapters.sync.adapter — PMO plan <-> Paperclip project/ticket sync (issue #1649).

---knowledge---
module_id: integrations.paperclip.adapters.sync.adapter
system: integrations
app: paperclip
solution_class: pattern
patterns: [adapter, write-read-half-of-existing-derivation]
derives_from: governance/pmo/plan.py
owner_sme: paperclip
tier: L1
interfaces: [SyncRefused, push_plan, pull_plan]
invariants: ""
gotchas: ""
related: ["#1649"]
do_not_duplicate: null
---knowledge---

``governance/pmo/plan.py::render_paperclip`` already derives the read-only
payload (project + milestones + tickets) a PMO plan would push to Paperclip;
this module is the missing write/read half named in that function's own
docstring ("if integrations/paperclip/adapters had a write path — issue
#1649"). It never re-derives the payload shape — it imports and reuses
``render_paperclip`` — and it never talks to the network directly: every call
goes through a ``PaperclipClient``-shaped object (the real one or, in tests,
the fake one below), same seam discipline as the rest of
``integrations/paperclip``.

Gated behind the ``enable_paperclip`` flag (``infra/feature-flags/registry.yaml``,
default off): ``push_plan``/``pull_plan`` refuse by name (``SyncRefused``,
naming the flag) when the flag reads off, fail-closed like every other reader
of that registry.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from . import flags

#: governance/pmo is a flat script package (no __init__.py) — mirror its own
#: cli.py's ``import plan as plan_module`` by putting it on sys.path.
_PMO_DIR = Path(__file__).resolve().parents[4] / "governance" / "pmo"
if str(_PMO_DIR) not in sys.path:
    sys.path.insert(0, str(_PMO_DIR))

import plan as plan_module  # noqa: E402


class SyncRefused(Exception):
    """The sync adapter refused — the flag was off, or a client call failed."""


def _require_enabled(*, enabled: Optional[bool], registry_path: Optional[Path]) -> None:
    on = flags.paperclip_enabled(registry_path) if enabled is None else enabled
    if not on:
        raise SyncRefused(
            f"paperclip sync refused: enable_paperclip is off "
            f"({flags.SERVICE_KEY!r} in infra/feature-flags/registry.yaml)"
        )


def push_plan(
    client: Any,
    document: dict[str, Any],
    *,
    enabled: Optional[bool] = None,
    registry_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Push one PMO plan document to Paperclip as ticket records.

    Reuses ``plan_module.render_paperclip`` for the payload, then calls
    ``client.create_issue`` per ticket. Returns the upstream records the
    client echoes back (each ``response.body``). Refused (``SyncRefused``)
    when the flag is off — never calls the client in that case.
    """
    _require_enabled(enabled=enabled, registry_path=registry_path)
    payload = plan_module.render_paperclip(document)
    created: list[dict[str, Any]] = []
    for ticket in payload["tickets"]:
        response = client.create_issue(ticket)
        created.append(response.body)
    return created


def pull_plan(
    client: Any,
    *,
    enabled: Optional[bool] = None,
    registry_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Pull the upstream tickets back as plan-shaped ticket records
    (``id``/``goal``/``blocked_by``/``kind`` — the same keys
    ``plan_module.paperclip_ticket`` populates), so a caller can diff a
    round trip against the plan it pushed. Refused when the flag is off.
    """
    _require_enabled(enabled=enabled, registry_path=registry_path)
    response = client.issues()
    records = response.body if isinstance(response.body, list) else response.body.get("issues", [])
    return [
        {
            "id": record["id"],
            "goal": record.get("goal", ""),
            "blocked_by": record.get("blocked_by", []),
            "kind": record.get("kind", "task"),
        }
        for record in records
    ]
