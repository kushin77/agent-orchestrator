"""portal.server.control_audit — exactly-once control: one effect, one record
(issue #555, RC-4 of EPIC #551).

WHY this exists. RC-3 (``portal/server/control_api.py``, issue #554) gives the
fleet a remote control channel: ``POST /api/control/<family>/<action>`` delivers
a declared verb to the lever the vocabulary names, and every refusal is decided
before anything is delivered. What it does **not** do is make a *retry* safe or
leave an *evidence trail*. Two gaps are left open by design, and this module is
both of them:

1. **A retried command is a second effect.** RC-3's guard
   (``InFlightCommands``) refuses a command id that is *still in flight* and
   stores nothing on completion — deliberately, so that answering a replay of a
   **finished** command is not a second, weaker copy of the store. This module is
   that store: a command id is minted, remembered, and a replay returns the
   original receipt instead of running the lever again.
2. **An applied command leaves no record.** ADR-0025 D3 makes an audit record a
   *required* property of a control action, not an option: an operator-requested
   verb may write fleet state only when it is identified **and audited** — exactly
   one record per applied command. This module appends that one record.

Consume, never coin. The record goes to the rails that already exist —
``telemetry/ledger/`` (the hash-chained per-tenant audit ledger, issue #31) and
the ``.fleet/slog.jsonl`` stream (``fleet/channel.py``'s own writer). No new
ledger is minted: ADR-0025's decision table refuses "a new control ledger" in
favour of the existing rails, under ADR-0012's one-authority rule, which is
restated as "no second server, no second identity, no second permission
vocabulary, **no second ledger**" (ADR-0025, decision 4). This module therefore
introduces **no store of its own**: the exactly-once index is a *projection of
the ledger rail* — the command id is written into the ledger record's
``evidence`` field as ``control-command:<id>`` (the same
``<rail>-<kind>:<pointer>`` convention ``telemetry/ledger/adapter.py`` already
uses for registry events), and the in-memory index is rebuilt from that rail on
construction. One effect, one record, one place records live.

The seam. ``control_api.CommandLedger`` fixes three calls and this class
implements exactly them — ``begin`` / ``finish`` / ``end`` — so the transport's
single choke point (``RemoteControl.apply_command``) needs no change to work with
it: the collaborator it builds in ``__init__`` is replaced by injecting this
object. A refusal is returned as a string (the transport answers ``409
duplicate_command``); a replay carries the **original receipt** as canonical JSON
after :data:`RECEIPT_SEPARATOR`, which is how "a retried verb returns the original
receipt" is delivered over a seam whose only return channel is a refusal string.

Two refusals RC-4 owns, and they are different answers:

* **a replay** — the same command id, for the same verb, by the same principal,
  with the same arguments. Nothing is delivered and the original receipt comes
  back. Exactly one record stays on the ledger, because the second command never
  became an effect.
* **a reordered or stolen id** — the same command id naming a *different* command
  (another verb, or another principal's). An id is spent once, by one principal,
  on one command; reusing it is refused rather than answered, so an id cannot be
  walked onto a different effect or taken over by another caller.

The refusal path, completed. RC-3 already refuses an unreachable lever with
``503 lever_unreachable``; this module's contribution is the other half of that
sentence — **nothing is written for a command that had no effect**. ``finish`` is
reached only on an applied command, so neither rail learns about a command that
was refused or unreachable, and a replay adds no record either. And since a record
is not optional, an effect that **cannot** be recorded must never read as success:
an un-writable rail raises ``503 audit_unavailable`` naming the applied-but-
unrecorded effect, and the id is still remembered as applied, so a retry after an
audit outage can never become a second effect.

Offline by construction: no sockets, no network egress, file writes only — and
only to the two rails above.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from portal.server.app import ApiError
from portal.server.fleet_authz import PLATFORM_ORG

REPO_ROOT = Path(__file__).resolve().parents[2]

__all__ = [
    "AppliedCommand",
    "ControlAudit",
    "DEFAULT_LEDGER_DIR",
    "EVIDENCE_PREFIX",
    "LEDGER_DIR_ENV",
    "RECEIPT_SEPARATOR",
    "REPLAY_MARKER",
    "CONFLICT_MARKER",
    "TENANT_ID",
    "load_fleet_channel",
]

#: The tenant whose chain a control record belongs to. The thing being
#: controlled is the fleet itself and nothing else, so the record lands on the
#: **platform** org's chain — the same org RC-3's capability gate evaluates at
#: (``portal.server.fleet_authz.PLATFORM_ORG``). Consumed, never restated.
TENANT_ID = PLATFORM_ORG

#: The ledger's ``evidence`` pointer convention: ``telemetry/ledger/adapter.py``
#: writes ``registry-event:<hash>`` from the registry rail to the audit rail, and
#: this module writes ``control-command:<commandId>`` from the control rail. It is
#: what makes the exactly-once index a *projection* of the ledger rather than a
#: second store.
EVIDENCE_PREFIX = "control-command:"

#: Where the deployment keeps the existing ``telemetry/ledger`` store. Its own
#: docs name ``/var/lib/audit``; ``AO_AUDIT_LEDGER_DIR`` overrides it, and the
#: repo-relative default keeps the fleet's own runtime state under ``.fleet/``.
LEDGER_DIR_ENV = "AO_AUDIT_LEDGER_DIR"
DEFAULT_LEDGER_DIR = Path(".fleet") / "audit-ledger"

#: The marker in a replay refusal, and the separator that carries the receipt.
REPLAY_MARKER = "was already applied; returning the original receipt"
RECEIPT_SEPARATOR = "originalReceipt="
#: The marker in a reordered / stolen command id refusal.
CONFLICT_MARKER = "is already spent on another command"

#: The ``.fleet/slog.jsonl`` line shape, inside the vocabulary
#: ``fleet/channel.py`` declares (``MESSAGE_TYPES`` / the ``operator|brain``
#: role set): the control path *is* the operator -> fleet direction (ADR-0025 D1).
SLOG_FROM = "operator"
SLOG_TO = "brain"
SLOG_TYPE = "result"
SLOG_SEVERITY = "info"
#: How much of the receipt the human-readable slog line carries (the record is
#: the ledger's job; the slog line is the live tail).
SLOG_BODY_LIMIT = 160

#: The private module name ``fleet/channel.py`` is loaded under, so the generic
#: basename ``channel`` cannot collide with another import on ``sys.path``
#: (the same reason ``portal/server/fleet.py`` loads ``console`` privately).
_CHANNEL_MODULE_NAME = "ao_fleet_channel"
_CHANNEL_CACHE: dict[str, Any] = {}

_LEDGER_MODULE: Any = None


def load_fleet_channel(repo_root: Path | str = REPO_ROOT) -> Any:
    """Import ``fleet/channel.py`` — the ONE writer of ``.fleet/slog.jsonl``.

    The slog is appended through the production writer
    (``fleet/channel.py::_slog``, which takes an exclusive ``flock`` and owns the
    line shape), never by re-implementing an append here. The module is cached
    per resolved repo root so the caller and a test address the *same* module
    object — which is how a redirected ``SLOG`` reaches the writer.
    """
    key = str(Path(repo_root).resolve())
    cached = _CHANNEL_CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(repo_root) / "fleet" / "channel.py"
    fleet_dir = str(path.parent)
    if fleet_dir not in sys.path:
        # ``fleet/channel.py`` imports its lane siblings by bare name (``runtime``).
        sys.path.insert(0, fleet_dir)
    spec = importlib.util.spec_from_file_location(_CHANNEL_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the fleet channel at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CHANNEL_MODULE_NAME] = module
    spec.loader.exec_module(module)
    _CHANNEL_CACHE[key] = module
    return module


def _load_ledger() -> Any:
    """Import the **existing** ``telemetry/ledger`` store (never a second ledger).

    ``telemetry/`` is a per-issue package directory with no ``__init__.py``, so
    the sibling ``ledger`` package is imported the way the rest of the repo
    imports it (``telemetry/audit/read_model.py`` does exactly this).
    """
    global _LEDGER_MODULE
    if _LEDGER_MODULE is None:
        telemetry_root = str(REPO_ROOT / "telemetry")
        if telemetry_root not in sys.path:
            sys.path.insert(0, telemetry_root)
        import ledger  # noqa: PLC0415  (after sys.path)

        _LEDGER_MODULE = ledger
    return _LEDGER_MODULE


@dataclass(frozen=True)
class AppliedCommand:
    """One command id that has already become an effect.

    ``receipt`` is the transport's own effect record verbatim
    (``EffectRecord.as_json()``), so a replay returns the original receipt
    **byte for byte** rather than a reconstruction. ``args`` is the caller's
    argv as recorded — ``None`` when the entry was rehydrated from the ledger
    rail, which carries the command's identity but not its argv; the honest
    consequence is that a rehydrated entry matches on verb + principal.
    """

    command_id: str
    verb: str
    actor: str
    args: Optional[tuple[str, ...]]
    receipt: dict[str, Any]
    ledger_seq: int
    ledger_hash: str

    def matches(self, command: Any) -> bool:
        """True when ``command`` is the same command this id was spent on."""
        if self.verb != command.verb or self.actor != command.actor:
            return False
        if self.args is None:
            # Rehydrated from the rail: the ledger record names the verb and the
            # principal, not the argv. Narrower than the in-process entry by
            # construction, and stated rather than pretended.
            return True
        return tuple(self.args) == tuple(command.argv)

    def refusal_kind(self, command: Any) -> str:
        """``"stolen"`` when the principal differs, else ``"reordered"``."""
        return "stolen" if self.actor != command.actor else "reordered"

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "commandId": self.command_id,
            "verb": self.verb,
            "actor": self.actor,
            "ledger": {"seq": self.ledger_seq, "hash": self.ledger_hash},
        }
        if self.args is not None:
            payload["args"] = list(self.args)
        return payload


class ControlAudit:
    """RC-4's record path: the exactly-once store and the audit append.

    It implements ``control_api.CommandLedger`` — ``begin`` / ``finish`` /
    ``end`` — and nothing else, so ``RemoteControl.apply_command`` can be handed
    this object as its ``commands`` collaborator without changing the call site.

    The in-flight half is RC-3's own ``InFlightCommands``, composed rather than
    restated; this class adds the exactly-once half on top of it.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str = REPO_ROOT,
        ledger_dir: Optional[Path | str] = None,
        tenant_id: str = TENANT_ID,
        store: Any = None,
        channel: Any = None,
        in_flight: Any = None,
        rehydrate: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.tenant_id = tenant_id
        self._lock = threading.RLock()
        #: id -> the command it was spent on. Rebuilt from the ledger rail.
        self._applied: dict[str, AppliedCommand] = {}

        if in_flight is None:
            from portal.server.control_api import InFlightCommands  # noqa: PLC0415

            in_flight = InFlightCommands()
        self._in_flight = in_flight

        if store is None:
            resolved = (
                Path(ledger_dir)
                if ledger_dir is not None
                else os.environ.get(LEDGER_DIR_ENV) or (self.repo_root / DEFAULT_LEDGER_DIR)
            )
            self.ledger_dir: Optional[Path] = Path(resolved)
            store = _load_ledger().open_ledger(str(self.ledger_dir))
        else:
            self.ledger_dir = None
        self.store = store

        self.channel = channel if channel is not None else load_fleet_channel(self.repo_root)

        if rehydrate:
            self._rehydrate()

    # -- the CommandLedger seam --------------------------------------------
    def begin(self, command: Any) -> Optional[str]:
        """Refuse a spent command id, or reserve a fresh one.

        Order matters. The exactly-once store is consulted **first**, because a
        command id that already became an effect is answered (a replay) or
        refused (a reordering) rather than delivered — and no reservation is
        taken for either, since nothing will be delivered. Only a genuinely fresh
        id reaches RC-3's in-flight guard, which keeps refusing a *duplicate in
        flight* exactly as it did before.
        """
        with self._lock:
            prior = self._applied.get(command.id)
        if prior is not None:
            where = f"ledger seq {prior.ledger_seq}" if prior.ledger_seq else "no rail record"
            if prior.matches(command):
                receipt = json.dumps(prior.receipt, sort_keys=True, ensure_ascii=True)
                return (
                    f"command {command.id!r} for {command.verb} {REPLAY_MARKER} "
                    f"({where}); nothing was delivered and nothing new was written. "
                    f"{RECEIPT_SEPARATOR}{receipt}"
                )
            kind = prior.refusal_kind(command)
            return (
                f"command {command.id!r} {CONFLICT_MARKER}: it was spent on "
                f"{prior.verb} by {prior.actor}, and this request is a {kind} "
                f"command for {command.verb} by {command.actor}. A command id "
                "names one command, once — refused, and nothing was delivered."
            )
        return self._in_flight.begin(command)

    def finish(self, command: Any, record: Any) -> None:
        """Append the ONE record for an applied command, on the existing rails.

        Reached only for a command that had an effect (the transport does not
        call it when the lever was unreachable, timed out or declined), so it
        cannot write a record for a command that did nothing. Idempotent by
        construction: a second call for an id already spent returns without
        writing, so a retry can never produce a second record.
        """
        receipt = record.as_json()
        with self._lock:
            if command.id in self._applied:
                return
            try:
                audit = self._append_to_ledger(command, record)
            except Exception as exc:  # fail closed: an unrecorded effect is not a success
                self._applied[command.id] = AppliedCommand(
                    command_id=command.id,
                    verb=command.verb,
                    actor=record.actor,
                    args=tuple(command.argv),
                    receipt=receipt,
                    ledger_seq=0,
                    ledger_hash="",
                )
                raise ApiError(
                    503,
                    "audit_unavailable",
                    f"{command.verb} was applied but its audit record could not be "
                    f"written to {type(exc).__name__}: {exc}. The effect is real and "
                    "unrecorded — this is not reported as a success, and the command "
                    "id is spent so a retry cannot become a second effect.",
                ) from exc
            self._append_to_slog(command, record, audit)
            self._applied[command.id] = AppliedCommand(
                command_id=command.id,
                verb=command.verb,
                actor=record.actor,
                args=tuple(command.argv),
                receipt=receipt,
                ledger_seq=int(audit.get("seq", 0)),
                ledger_hash=str(audit.get("hash", "")),
            )

    def end(self, command: Any) -> None:
        """Release the reservation ``begin`` took (RC-3's guard, unchanged)."""
        self._in_flight.end(command)

    # -- the two rails -----------------------------------------------------
    def _append_to_ledger(self, command: Any, record: Any) -> dict[str, Any]:
        """One record on the **existing** ``telemetry/ledger`` chain.

        Every field is the rail's own, and every value is read from the effect
        record the transport produced — the audit action the vocabulary declares
        for the verb, the principal the effect is attributed to, and the lever it
        was delivered by. ``evidence`` carries the ``control-command:<id>``
        pointer that makes the exactly-once index a projection of this rail.
        No sensitive payload is attached, so the append needs no tenant key and
        cannot fall back to plaintext.
        """
        return self.store.append(
            self.tenant_id,
            actor=record.actor,
            action=record.audit_action or record.verb,
            resource=record.lever,
            evidence=f"{EVIDENCE_PREFIX}{command.id}",
        )

    def _append_to_slog(self, command: Any, record: Any, audit: Mapping[str, Any]) -> None:
        """One line on the **existing** ``.fleet/slog.jsonl`` stream.

        Appended through ``fleet/channel.py::_slog`` — the production writer, so
        the line shape, the locking and the ``.fleet`` location stay that
        module's business. The line stays inside the declared message vocabulary
        (``type: result``, ``operator`` -> ``brain``), names the command in
        ``correlation_id``, and points at the ledger record it is the live tail
        of.
        """
        summary = " ".join((record.output or "").split())
        if len(summary) > SLOG_BODY_LIMIT:
            summary = summary[:SLOG_BODY_LIMIT] + "..."
        seq = audit.get("seq")
        rail = f", ledger seq {seq}" if seq else ""
        body = (
            f"{record.verb} [{record.effect_class}] applied by {record.actor} "
            f"via {record.lever} (exit {record.exit_code}{rail})"
        )
        if summary:
            body = f"{body}: {summary}"
        self.channel._slog(  # noqa: SLF001  (the rail's own writer)
            {
                "ts": record.requested_at,
                "id": f"ctl-{command.id}",
                "from": SLOG_FROM,
                "to": SLOG_TO,
                "type": SLOG_TYPE,
                "correlation_id": command.id,
                "severity": SLOG_SEVERITY,
                "body": body,
            }
        )

    # -- the store is a projection of the rail -----------------------------
    def _rehydrate(self) -> None:
        """Rebuild the exactly-once index from the ledger rail.

        This is what makes the store durable without a second store: the ids
        already spent are exactly the ids the rail carries a
        ``control-command:<id>`` record for. A replay after a restart therefore
        cannot become a second effect — it is refused, naming the ledger record
        it already owns.
        """
        for entry in self.ledger_records():
            evidence = entry.get("evidence") or ""
            if not isinstance(evidence, str) or not evidence.startswith(EVIDENCE_PREFIX):
                continue
            command_id = evidence[len(EVIDENCE_PREFIX) :]
            if not command_id or command_id in self._applied:
                continue
            self._applied[command_id] = AppliedCommand(
                command_id=command_id,
                verb=str(entry.get("action") or ""),
                actor=str(entry.get("actor") or ""),
                args=None,
                receipt={
                    "commandId": command_id,
                    "verb": str(entry.get("action") or ""),
                    "actor": str(entry.get("actor") or ""),
                    "lever": entry.get("resource"),
                    "ledger": {"seq": entry.get("seq"), "hash": entry.get("hash")},
                    "rehydrated": True,
                },
                ledger_seq=int(entry.get("seq") or 0),
                ledger_hash=str(entry.get("hash") or ""),
            )

    # -- read surface ------------------------------------------------------
    def ledger_records(self) -> list[dict[str, Any]]:
        """This tenant's ``telemetry/ledger`` chain, in chain order."""
        return [dict(entry) for entry in self.store.records(self.tenant_id)]

    def records_for(self, command_id: str) -> list[dict[str, Any]]:
        """Every ledger record this command id owns (exactly one, once applied)."""
        pointer = f"{EVIDENCE_PREFIX}{command_id}"
        return [entry for entry in self.ledger_records() if entry.get("evidence") == pointer]

    def applied(self, command_id: str) -> Optional[AppliedCommand]:
        """The command this id was spent on, or ``None``."""
        with self._lock:
            return self._applied.get(command_id)

    def slog_records(self) -> list[dict[str, Any]]:
        """The ``.fleet/slog.jsonl`` lines this module wrote, parsed."""
        path = Path(self.channel.SLOG)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def slog_records_for(self, command_id: str) -> list[dict[str, Any]]:
        """The slog lines this command id owns (exactly one, once applied)."""
        return [
            entry
            for entry in self.slog_records()
            if entry.get("correlation_id") == command_id
        ]
