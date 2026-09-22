"""Live sync — pulls tickets, heartbeats and budgets through the paperclip
seam and freezes the pull as one validated, audited record (issue #888).

This is the `module-brief` surface's missing `live_sync` evidence
(`governance/conformance/surfaces.py` `LIVE_TOKENS`): a running module, not a
data file, that reads real state on every call:

* **tickets** and **budgets** ride the existing HTTP seam
  (:class:`integrations.paperclip.client.PaperclipClient`) — the same
  ``Transport`` protocol the rest of the package uses, so tests replay them
  offline through :class:`~integrations.paperclip.client.FixtureTransport`
  and the gate never touches the network (issue #428's own rule, reused here);
* **heartbeats** are derived from the fleet's own local beat, the same way
  :mod:`integrations.paperclip.adapters.heartbeat.adapter` already does —
  injectable as ``heartbeat_reader`` so a test can supply a canned reader
  instead of a real ``.fleet/`` tree;
* every pull is authenticated first, through the boundary pipeline in
  :mod:`integrations.paperclip.auth.guard` (issue #412) — an unauthenticated
  or under-scoped caller never reaches the transport at all;
* the assembled record is validated against the frozen ``sync.schema.json``
  (the repository's own JSON-Schema subset,
  :mod:`integrations.paperclip.mapping`) before anything is written; a record
  that fails validation is refused by name and **never appended** to the
  trail — a false-green audit line would be worse than no line;
* exactly one line is appended per successful sync, to an append-only trail
  (default ``.verify/paperclip-live-sync-audit.jsonl``, mirroring
  :mod:`integrations.paperclip.reporting.audit`).

Refusals are named, never silent:

======================== =========================================
Code                      Refused
======================== =========================================
``SYNC-UNAUTHENTICATED``  no/invalid credential (401 at the boundary)
``SYNC-FORBIDDEN``        a known caller without the permission (403)
``SYNC-PAYLOAD-INVALID``  the assembled record fails ``sync.schema.json``
``SYNC-SCHEMA-FROZEN``    ``sync.schema.json`` is missing, unreadable or not
                          the frozen ``$id`` (CANNOT-ASSESS, not a pass)
======================== =========================================

---knowledge---
module_id: integrations.paperclip.reporting.sync.live
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [SyncRefusal, schema_path, load_schema, SyncRecord, authenticate, run_sync]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.paperclip import mapping as mapping_mod  # noqa: E402
from integrations.paperclip.auth import guard as guard_mod  # noqa: E402
from integrations.paperclip.auth.model import AuthError  # noqa: E402
from integrations.paperclip.client import PaperclipClient  # noqa: E402

#: The schema file, beside this module (it travels with the package).
SCHEMA_FILE = "sync.schema.json"
SCHEMA_ID = "ao.paperclip-live-sync/v1"

#: The default trail. `.verify/` is gitignored, so a sync never dirties the tree.
DEFAULT_AUDIT_TRAIL = Path(".verify") / "paperclip-live-sync-audit.jsonl"

#: The default heartbeat rungs a sync collects (mirrors `fleet/*.heartbeat.json`).
DEFAULT_RUNGS: Sequence[str] = ("monitor", "brain", "terminal")

CODE_UNAUTHENTICATED = "SYNC-UNAUTHENTICATED"
CODE_FORBIDDEN = "SYNC-FORBIDDEN"
CODE_PAYLOAD_INVALID = "SYNC-PAYLOAD-INVALID"
CODE_SCHEMA_FROZEN = "SYNC-SCHEMA-FROZEN"


class SyncRefusal(Exception):
    """A named refusal — the code is the thing that failed, never prose."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def schema_path(directory: Optional[Path] = None) -> Path:
    base = Path(directory) if directory is not None else Path(__file__).resolve().parent
    return base / SCHEMA_FILE


def load_schema(directory: Optional[Path] = None) -> Dict[str, Any]:
    """Read the frozen sync schema. Unreadable/wrong-$id is a named refusal."""
    path = schema_path(directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SyncRefusal(CODE_SCHEMA_FROZEN, f"unreadable: {path} ({exc})") from exc
    except ValueError as exc:
        raise SyncRefusal(CODE_SCHEMA_FROZEN, f"not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict) or data.get("$id") != SCHEMA_ID:
        raise SyncRefusal(
            CODE_SCHEMA_FROZEN,
            f"$id is {data.get('$id') if isinstance(data, dict) else None!r}, expected {SCHEMA_ID!r}",
        )
    return data


@dataclass(frozen=True)
class SyncRecord:
    """One live-sync pull — the shape `sync.schema.json` freezes."""

    ts: int
    company: str
    principal: str
    tickets: List[Dict[str, Any]] = field(default_factory=list)
    heartbeats: List[Dict[str, Any]] = field(default_factory=list)
    budgets: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "ts": self.ts,
            "company": self.company,
            "principal": self.principal,
            "counts": {
                "tickets": len(self.tickets),
                "heartbeats": len(self.heartbeats),
                "budgets": len(self.budgets),
            },
            "tickets": self.tickets,
            "heartbeats": self.heartbeats,
            "budgets": self.budgets,
        }


def authenticate(
    headers: Mapping[str, str],
    *,
    root: Path,
    company: str,
    secret: str,
    now: int,
    permission: str = "run:read",
) -> str:
    """AuthN + AuthZ at the boundary; returns the principal's actor id or refuses."""
    try:
        context = guard_mod.guard_request(
            "GET",
            headers,
            root=root,
            company=company,
            secret=secret,
            now=now,
            permission=permission,
        )
    except AuthError as exc:
        code = CODE_FORBIDDEN if exc.status == 403 else CODE_UNAUTHENTICATED
        raise SyncRefusal(code, str(exc)) from exc
    return context.principal.actor


def _pull_tickets(client: PaperclipClient) -> List[Dict[str, Any]]:
    body = client.issues().body or {}
    issues = body.get("issues") if isinstance(body, dict) else None
    return list(issues) if isinstance(issues, list) else []


def _pull_budgets(
    budget_builder: Callable[[Path], Any],
    root: Path,
) -> List[Dict[str, Any]]:
    report = budget_builder(root)
    records = getattr(report, "records", None)
    if not records:
        return []
    return [dict(record) for record in records]


def _pull_heartbeats(
    heartbeat_reader: Callable[..., Dict[str, Any]],
    root: Path,
    rungs: Sequence[str],
    *,
    session_id: str,
    now_iso: str,
) -> List[Dict[str, Any]]:
    beats: List[Dict[str, Any]] = []
    for rung in rungs:
        try:
            beats.append(heartbeat_reader(root, rung, session_id=session_id, now=now_iso))
        except Exception:  # noqa: BLE001 - a stale/absent rung is not a sync failure
            continue
    return beats


def run_sync(
    headers: Mapping[str, str],
    *,
    root: Path,
    company: str,
    secret: str,
    now: int,
    client: PaperclipClient,
    permission: str = "run:read",
    rungs: Sequence[str] = DEFAULT_RUNGS,
    session_id: str = "live-sync",
    heartbeat_reader: Optional[Callable[..., Dict[str, Any]]] = None,
    budget_builder: Optional[Callable[[Path], Any]] = None,
    audit_trail: Optional[Path] = None,
    schema_directory: Optional[Path] = None,
) -> SyncRecord:
    """Pull tickets/heartbeats/budgets, validate, audit — or refuse by name.

    Fails closed at every stage: an unauthenticated/under-scoped caller never
    reaches the transport (``SYNC-UNAUTHENTICATED`` / ``SYNC-FORBIDDEN``), and
    an assembled record that does not satisfy the frozen schema is refused
    (``SYNC-PAYLOAD-INVALID``) and **never appended** to the audit trail.
    """
    root = Path(root)
    actor = authenticate(
        headers, root=root, company=company, secret=secret, now=now, permission=permission
    )

    if heartbeat_reader is None:
        from integrations.paperclip.adapters.heartbeat import adapter as heartbeat_mod

        heartbeat_reader = heartbeat_mod.derive_heartbeat
    if budget_builder is None:
        from integrations.paperclip import budget as budget_mod

        budget_builder = budget_mod.build

    tickets = _pull_tickets(client)
    budgets = _pull_budgets(budget_builder, root)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    heartbeats = _pull_heartbeats(
        heartbeat_reader, root, rungs, session_id=session_id, now_iso=now_iso
    )

    record = SyncRecord(
        ts=now,
        company=company,
        principal=actor,
        tickets=tickets,
        heartbeats=heartbeats,
        budgets=budgets,
    )
    payload = record.to_json()

    schema = load_schema(schema_directory)
    findings = mapping_mod.validate(payload, schema)
    if findings:
        raise SyncRefusal(CODE_PAYLOAD_INVALID, "; ".join(findings))

    trail = Path(audit_trail) if audit_trail is not None else (root / DEFAULT_AUDIT_TRAIL)
    trail.parent.mkdir(parents=True, exist_ok=True)
    with trail.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")

    return record
