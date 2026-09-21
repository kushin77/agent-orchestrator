"""portal.server.surface_state — the runtime surface rollback overlay (#802).

---knowledge---
module_id: portal.server.surface_state
system: portal
app: server
solution_class: pattern
patterns: [rollback-overlay, join-not-own]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [overlay_path, read_rollbacks, is_rolled_back, rollback_record, record_rollback, clear_rollback]
invariants: ""
gotchas: ""
related: ["#802"]
do_not_duplicate: null
---knowledge---

WHY this exists. Every console surface is gated by its declaration in
``infra/feature-flags/registry.yaml`` (``surfaces.<name>.default``), read
fail-closed by :func:`portal.server.fleet.read_surface_default`. A declaration is
committed IaC: it is the **promotion** half of the rollout contract. What a
committed declaration cannot express is the **withdrawal** half — a surface that
is promoted, that then fails its own health check, must go dark *now*, not at the
next reviewed commit. Issue #802 asks for that rollback anchor; this module is
the runtime half of it.

A one-document overlay, in the console's own runtime directory, that
``read_surface_default`` consults **before** the registry. A surface named there
reads ``"off"`` whatever the registry declares, so a rollback is one atomic file
write away: no deploy and no commit. What it is read *by* is the surface
**reader** — the CLI, the rollback anchor and the readiness signal take the
rollback on their next read, and a long-running process that resolved its flags
once at boot takes it on its next resolve. That is deliberate: the overlay is the
source the reader consults rather than an in-process toggle, so a surface that
was taken dark stays dark across a restart instead of only until the next one.

Fail closed, exactly like the declaration it overrides. A document that cannot be
read is **not** assumed to be disengaged: :func:`read_rollbacks` returns ``None``
(cannot assess) and the reader refuses the surface, because a kill switch nobody
can read must never be assumed to be off. An **absent** document is the normal,
healthy case — no rollback is engaged and the registry's declaration stands. That
asymmetry is the whole safety property, and it is the same one the registry
reader already has (a missing registry is not "everything on").

Two writers, one shape:

* ``infra/rollout/surface_guard.py`` — the rollback anchor — records a rollback
  when the surface's own readiness signal fails;
* an operator (or the CLI's ``--clear``) can undo one with
  :func:`clear_rollback`, because the round trip is what makes this a *rollback*
  and not a one-way kill.

Scope, stated precisely: this overlay is consulted by
``portal.server.fleet.read_surface_default`` — the reader every **portal**
surface uses, the console included. Two sibling pillars keep their *own* copies
of that fail-closed reader (``gateway/chat/flags.py``,
``telemetry/observability/exposition.py``); a rollback engaged here does not
reach them, and that duplication — three copies of one contract — is reported
rather than papered over here.

Runtime state, never committed: the document lives under ``.rollout/`` (the
explicit-argument -> environment-variable -> repo-local default resolution
``portal/server/control_audit.py`` uses for its rails), so a test points it at a
scratch document and the deployment at a durable one.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

#: Explicit argument -> this environment variable -> :data:`DEFAULT_STATE`.
STATE_ENV = "AO_SURFACE_STATE"
#: The console's own runtime directory for the rollback overlay.
DEFAULT_STATE = Path(".rollout") / "surface-state.json"
#: The document's schema version.
SCHEMA_VERSION = 1
#: Where the engaged rollbacks live inside the document.
ROLLBACKS_KEY = "rollbacks"
#: The only stage a rollback can engage (the declared rollback target).
ROLLBACK_STAGE = "off"


def overlay_path(repo_root: Path | str, *, path: Optional[Path | str] = None) -> Path:
    """The overlay document's location (explicit -> env -> repo-local default)."""
    if path is not None:
        return Path(path)
    configured = os.environ.get(STATE_ENV)
    if configured:
        return Path(configured)
    return Path(repo_root) / DEFAULT_STATE


def read_rollbacks(
    repo_root: Path | str, *, path: Optional[Path | str] = None
) -> Optional[Mapping[str, Mapping[str, Any]]]:
    """The engaged rollbacks, or ``None`` when the document cannot be read.

    * absent -> ``{}`` — nothing is rolled back, the declaration stands;
    * present, readable, well-formed -> its ``rollbacks`` mapping;
    * present but unreadable, unparseable, or malformed -> ``None``, i.e.
      **cannot assess**, which :func:`is_rolled_back` and the surface reader both
      treat as "rolled back" (fail closed).
    """
    target = overlay_path(repo_root, path=path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return None
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    rollbacks = document.get(ROLLBACKS_KEY)
    if rollbacks is None:
        return {}
    if not isinstance(rollbacks, dict):
        return None
    for key, value in rollbacks.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            return None
    return rollbacks


def is_rolled_back(
    repo_root: Path | str, surface: str, *, path: Optional[Path | str] = None
) -> bool:
    """True when ``surface`` is under an engaged rollback — or cannot be assessed."""
    rollbacks = read_rollbacks(repo_root, path=path)
    if rollbacks is None:
        return True
    return surface in rollbacks


def rollback_record(
    repo_root: Path | str, surface: str, *, path: Optional[Path | str] = None
) -> Optional[Mapping[str, Any]]:
    """The record engaged for ``surface``, or ``None`` when none is engaged."""
    rollbacks = read_rollbacks(repo_root, path=path)
    if rollbacks is None:
        return {"stage": ROLLBACK_STAGE, "reason": "the rollback overlay cannot be read"}
    return rollbacks.get(surface)


def record_rollback(
    repo_root: Path | str,
    surface: str,
    *,
    reason: str,
    actor: str,
    at: Optional[str] = None,
    path: Optional[Path | str] = None,
) -> Path:
    """Engage a rollback for ``surface``; returns the document that was written.

    Merge-preserving and atomic: another surface's engaged rollback is never
    dropped, and no reader ever sees a half-written document. A document that
    could not be read is **replaced** rather than trusted: the reader had already
    failed closed on it, so replacing it repairs the switch instead of
    disengaging anything.
    """
    target = overlay_path(repo_root, path=path)
    existing = read_rollbacks(repo_root, path=target) or {}
    rollbacks = {name: dict(record) for name, record in existing.items()}
    rollbacks[surface] = {
        "stage": ROLLBACK_STAGE,
        "reason": reason,
        "actor": actor,
        "at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write(target, {"schema_version": SCHEMA_VERSION, ROLLBACKS_KEY: rollbacks})
    return target


def clear_rollback(
    repo_root: Path | str, surface: str, *, path: Optional[Path | str] = None
) -> bool:
    """Disengage ``surface``'s rollback; True when one was engaged.

    Raises ``ValueError`` when the document exists but cannot be read: which
    surfaces it names is unknowable, so silently writing a fresh document would
    drop every other engaged rollback. Fail closed and say so.
    """
    target = overlay_path(repo_root, path=path)
    existing = read_rollbacks(repo_root, path=target)
    if existing is None:
        raise ValueError(
            f"the rollback overlay at {target} cannot be read; "
            "clear it by hand rather than dropping the rollbacks it may carry"
        )
    if surface not in existing:
        return False
    remaining = {
        name: dict(record) for name, record in existing.items() if name != surface
    }
    if not remaining:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return True
    _write(target, {"schema_version": SCHEMA_VERSION, ROLLBACKS_KEY: remaining})
    return True


def _write(target: Path, document: Mapping[str, Any]) -> None:
    """Write ``document`` to ``target`` atomically, creating its directory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(f"{target.name}.tmp")
    scratch.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(scratch, target)
