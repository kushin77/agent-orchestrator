"""A live projection of dispatch's real state (issue #885).

``status`` already prints the active milestone, the frontier and the live
claims, computed fresh from the real ledger and snapshot on every call — but
nothing captured *that projection itself* as an artifact a drift check could
compare against a second, independent read of the same store. This module is
that projection, exposed through the existing ``status`` verb as
``status --live`` (never a new top-level verb — the gate `check-control-verbs.sh`
pins the verb set):

* :func:`project` reads the same ledger and snapshot ``cmd_status`` already
  reads (``claims.active_claims`` + ``order.frontier``/``order.active_milestone``)
  and returns one JSON-serialisable document: the live claim set, the frontier
  issue, and the wave a ready (unclaimed, unblocked) issue currently sits in
  (from ``queue.yaml`` via ``owner_queue``).
* Because it is a pure read of the same two stores, a second independent call
  to :func:`project` against the same paths must agree with the first
  byte-for-byte — the drift the gate provocation exercises is a *stale copy*
  (a caller pinning a serialised projection instead of re-reading the store).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import claims  # noqa: E402
import order  # noqa: E402
import owner_queue  # noqa: E402
from model import Snapshot  # noqa: E402


def _ready_wave(snapshot: Snapshot, live: dict, queue_data: dict | None) -> str | None:
    """The name of the earliest wave holding a ready (open, unblocked, unclaimed) issue."""
    if not queue_data:
        return None
    for wave in queue_data.get("waves") or []:
        for number in wave.get("issues") or []:
            issue = snapshot.get(number)
            if issue is None or issue.closed or number in live:
                continue
            if snapshot.blockers_open(issue):
                continue
            return str(wave.get("name") or "")
    return None


def project(
    snapshot: Snapshot,
    ledger: Path | str = claims.DEFAULT_CLAIMS_DIR,
    queue_path: Path | str | None = None,
) -> Dict[str, Any]:
    """Compute the live projection: claim set + frontier + ready wave.

    Reads nothing but the real ledger (``ledger``), the given ``snapshot``, and
    the committed ``queue.yaml`` (or ``queue_path`` in a test) — never a cached
    or serialised copy of any of them, so two calls against the same store
    always agree.
    """
    events = claims.read_ledger(ledger)
    live = claims.active_claims(events)
    milestone = order.active_milestone(snapshot, frozenset())
    frontier_issue = order.frontier(snapshot, milestone) if milestone else None
    try:
        queue_data = owner_queue.load(queue_path) if queue_path is not None else owner_queue.load()
    except Exception:
        queue_data = None
    return {
        "schema": "ao.dispatch/live-v1",
        "active_milestone": milestone or None,
        "frontier": frontier_issue.number if frontier_issue is not None else None,
        "live_claims": [
            {"issue": issue, "agent": event.agent, "lane": event.lane, "since": event.at, "reason": event.reason}
            for issue, event in sorted(live.items())
        ],
        "ready_wave": _ready_wave(snapshot, live, queue_data),
    }


def render(projection: Dict[str, Any]) -> str:
    """Human-readable rendering, appended to ``status``'s existing output."""
    lines = [
        "live: milestone={} frontier={} ready_wave={}".format(
            projection["active_milestone"] or "<none>",
            f"#{projection['frontier']}" if projection["frontier"] is not None else "<none>",
            projection["ready_wave"] or "<none>",
        )
    ]
    for record in projection["live_claims"]:
        lines.append(
            "  #{issue} held by {agent} ({lane}) since {since} reason={reason}".format(**record)
        )
    return "\n".join(lines)
