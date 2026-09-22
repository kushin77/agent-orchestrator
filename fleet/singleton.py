"""Singleton guard: one loop per rung, or the fleet double-dispatches.

---knowledge---
module_id: fleet.singleton
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [lock_path, holder_pid, acquire, guard]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

Observed live (2026-09-13): two dispatcher loops were running at once — `cmd_watch`
returns the oldest pending directive without removing it, so both loops picked up
the same order, both tried to claim the same issue, and the channel logged every
escalation twice. Nothing in the transport prevents it: the mailbox is a
directory, and a directory has as many readers as you start.

Each loop therefore holds an exclusive `flock` on `.fleet/<rung>.lock` for its
whole lifetime. A second loop refuses to start and names the pid that holds the
lock — an honest refusal, not a silent second worker.
"""

from __future__ import annotations

import json
from pathlib import Path

import lease
import runtime

FLEET = runtime.FLEET_DIR


def lock_path(rung: str) -> Path:
    return FLEET / f"{rung}.lock"


def holder_pid(rung: str) -> str:
    """The pid the running loop recorded in its heartbeat, for the refusal text."""
    try:
        beat = json.loads((FLEET / f"{rung}.heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    return str(beat.get("pid", "unknown"))


def acquire(rung: str) -> int | None:
    """Take the rung's lock; return the fd on success, or None when held.

    The fd is intentionally leaked to the process: releasing it (a `finally`, a
    GC) would unlock the rung, and the lock must live exactly as long as the
    loop does. Delegates to `lease.fcntl_try_lock`, the same open+flock+write
    sequence `gatelock.py` and `fleet/lease.py`'s `FcntlLease` use, so this
    module does not keep its own copy of it (issue #713).
    """
    return lease.fcntl_try_lock(lock_path(rung))


def guard(rung: str, start_cmd: str) -> bool:
    """True when this process owns the rung; prints the refusal when it does not."""
    if acquire(rung) is None:
        print(
            f"[{rung}] REFUSED — another {rung} loop already holds {lock_path(rung)} "
            f"(pid {holder_pid(rung)}). Two loops double-dispatch the same directive; "
            f"stop the other one first, or reuse it.",
            flush=True,
        )
        print(f"[{rung}] to restart the fleet cleanly: {start_cmd}", flush=True)
        return False
    return True
