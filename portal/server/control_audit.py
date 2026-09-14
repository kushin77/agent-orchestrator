"""portal.server.control_audit — exactly-once control: one effect, one record.

WHY this exists (issue #555, RC-4 of EPIC #551). RC-3 shipped the control
channel and its refusals; it records nothing. Its ledger, ``InFlightCommands``,
answers "this id is *still running*" and forgets the id the moment the command
returns — so a retried ``pause`` is a second effect, and an operation whose
lever cannot be reached leaves no trace that distinguishes it from one that
never happened. RC-4 closes both halves:

* **one effect, one record.** A command id is minted once and bound to what it
  ordered; a replay is refused rather than applied twice, and is handed the
  original receipt; the effect is recorded once, on the rails that already
  exist — never a second ledger;
* **no silent no-op.** A command that had no effect (an unreachable lever, a
  lever that declined) writes no record, and a command whose effect *was*
  applied but whose record could not be written is reported as exactly that
  rather than returned as a success.

The seam. ``portal/server/control_api.py`` funnels every mutating request
through :meth:`RemoteControl.apply_command` and its three-call collaborator —
``begin`` / ``finish`` / ``end``. :class:`ControlAudit` is that collaborator,
and the transport changes in exactly one line (the collaborator built in
``RemoteControl.__init__``).

The one-line budget fixes the shape of the answer to a replay. RC-3 declares the
contract this class implements: *"``None`` means 'proceed'; a string is the
refusal to answer with (a duplicate already in flight, or — once RC-4 lands it —
a replayed or reordered command id)"*. So a replay is answered with a refusal,
and the **original receipt is handed back inside it**, verbatim, after
:data:`RECEIPT_MARKER` — a 200-shaped replay would need a second edit to the
transport's call site, which the issue's lane budget forbids. What the
acceptance requires is honoured exactly: a replay never reaches the lever, never
writes a second record, and the caller is given the original receipt.

What a replay is, and what it is not. A command id is bound to the command it
ordered — the verb, its arguments and the acting principal. The same id
presented with the same binding is a **replay** (receipt handed back); the same
id presented by a different principal is a **stolen** id; the same id coming
back for a different verb or arguments is a **reordered** id. The last two are
refused *without* the receipt: a thief must not be handed someone else's effect,
and a reordering must not be answered as though it were the original command.

Two rails, and no third ledger (ADR-0012/0015). Every applied command appends
**one** record to the tenant chain of the existing ``telemetry/ledger`` store —
through ``LedgerStore``, so the record is hash-chained and tamper-evident, and
``verify_ledger`` reads it back — and **one** line to the fleet's existing
``.fleet/slog.jsonl`` stream, in the shape the fleet's own writer uses
(:func:`fleet.channel._slog`; the key set is pinned by a test against that
function so the two cannot drift). The portal's own ``auditlog.py`` chain is
deliberately **not** used: it is the console's offline chain for console views,
and a control action is fleet state (ADR-0025 D3 names ``telemetry/ledger`` and
``.fleet/slog.jsonl``).

Rails are resolved the way this pillar already resolves a runtime store
(``portal/server/chat.py``): an explicit argument, then an environment variable,
then a repo-local runtime directory. The receipt always names the rails that
actually took the record, so "where did my record go?" is answered by the
receipt rather than by convention.

What is left for a deployment. The in-process index is what makes a replay
cheap; the rail is what makes it durable. :meth:`ControlAudit.recover` rebuilds
the index from the chain so that a restarted console refuses to re-apply a
command the rail already remembers — reporting the receipt as *unavailable*
rather than inventing one, because the rail's evidence field, not its payload,
is what the recovery reads (a payload would need the tenant's encryption key,
and a console holds none).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Explicit argument -> this environment variable -> :data:`DEFAULT_LEDGER_DIR`.
#: (The pattern ``portal/server/chat.py`` uses for its own store: an overridable
#: rail, so a test points it at a scratch directory and the deployment points it
#: at a durable one.)
LEDGER_DIR_ENV = "AO_LEDGER_DIR"
#: Explicit argument -> this environment variable -> the fleet's own stream.
SLOG_ENV = "AO_CONTROL_SLOG"
#: Where the fleet's runtime directory is, when it is not ``<repo>/.fleet``.
FLEET_DIR_ENV = "AO_FLEET_DIR"
#: The console's own runtime directory for the control audit rail.
DEFAULT_LEDGER_DIR = Path(".portal") / "control" / "ledger"
#: The fleet's runtime directory, and the stream every fleet message is logged to.
DEFAULT_FLEET_DIR = Path(".fleet")
SLOG_FILENAME = "slog.jsonl"

#: The one tenant a control action is recorded on: the fleet itself is what is
#: being controlled, and ADR-0025 D2.3 fixes the resource at the platform org.
DEFAULT_TENANT = "platform"

#: A minted command id is a durable key, so it is not shortened.
MINT_PREFIX = "cmd_"
#: The prefix of the durable id of one *record* (distinct from the command id:
#: a receipt is addressable by either).
RECORD_PREFIX = "rc_"
#: The marker after which a replay refusal carries the original receipt, as
#: canonical JSON. Documented so a client parses it rather than guessing.
RECEIPT_MARKER = "receipt: "
#: The chain record's evidence pointer back to the command that ordered it.
EVIDENCE_PREFIX = "control-command:"
#: The stream record's shape: the fleet's own writer trims a body to 200 chars.
BODY_LIMIT = 200
#: Who the stream record is from and to, and what kind of record it is. The
#: fleet's role vocabulary (``operator``/``brain``/``sister``/``subagent``) names
#: *agents*; a control action is not one, so it is named for what it is rather
#: than dressed as a role it is not.
STREAM_FROM = "control"
STREAM_TO = "fleet"
STREAM_TYPE = "control"


class AuditUnavailable(RuntimeError):
    """The effect could not be recorded (fail closed, never a silent success)."""


class CommandLike(Protocol):
    """The fields of ``control_api.Command`` this collaborator reads."""

    id: str
    verb: str
    effect_class: str
    capability: str
    audit_action: Optional[str]
    actor: str
    lever: str
    argv: Sequence[str]
    idempotent: bool
    requested_at: str
    mutates: bool


class EffectRecordLike(Protocol):
    """The fields of ``control_api.EffectRecord`` this collaborator reads."""

    command_id: str
    verb: str
    effect_class: str
    capability: str
    audit_action: Optional[str]
    actor: str
    idempotent: bool
    lever: str
    args: Sequence[str]
    exit_code: int
    output: str
    requested_at: str

    def as_json(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# minting, binding, and the canonical form of a receipt
# ---------------------------------------------------------------------------
def utc_now() -> str:
    """The RFC 3339 UTC second the fleet's own writers stamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mint(prefix: str) -> str:
    """A fresh id in ``prefix`` — full-width, because these are durable keys.

    The transport mints a short id for a caller that sends none (a per-request
    reservation key); this module's ids are *recorded*: a command id that has
    been bound, and the id of the record that was written for it. A shortened
    durable key is a collision waiting for a busy day, so these are not.
    """
    return f"{prefix}{uuid.uuid4().hex}"


def mint_command_id() -> str:
    """Mint one durable command id."""
    return _mint(MINT_PREFIX)


def mint_record_id() -> str:
    """Mint the durable id of one audit record (distinct from its command id)."""
    return _mint(RECORD_PREFIX)


def canonical(payload: Any) -> str:
    """Deterministic JSON — the form a receipt is handed back in, byte for byte."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def command_binding(command: CommandLike) -> str:
    """The identity of the *command* an id was used for.

    ``sha256`` over the verb, its arguments and the acting principal: what the
    order was, who gave it. An id reused for a different order (or by a
    different principal) is not the same command, and must not be answered as
    though it were.
    """
    return hashlib.sha256(
        canonical(
            {
                "verb": command.verb,
                "args": [str(item) for item in command.argv],
                "actor": command.actor,
            }
        ).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# what one applied command left behind
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RecordedCommand:
    """One applied command, as the store remembers it.

    ``receipt_json`` is the transport's own effect record (``EffectRecord``) in
    canonical JSON — stored as text so the value that is handed back on a replay
    is *the original bytes*, never a re-serialisation that might differ. It is
    empty for a record rebuilt by :meth:`ControlAudit.recover`: the rail carries
    the evidence of the effect (which is what makes a second application
    impossible), not the receipt body, and an unavailable receipt is reported as
    unavailable rather than reconstructed.
    """

    command_id: str
    record_id: str
    verb: str
    actor: str
    binding: str
    applied_at: str
    ledger_tenant: str
    ledger_action: str
    ledger_seq: int
    ledger_hash: str
    stream_id: str
    stream_path: str
    receipt_json: str = ""
    recovered: bool = False

    @property
    def receipt(self) -> Optional[dict[str, Any]]:
        """The receipt as the caller received it, or ``None`` if unavailable."""
        if not self.receipt_json:
            return None
        return json.loads(self.receipt_json)

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "commandId": self.command_id,
            "recordId": self.record_id,
            "verb": self.verb,
            "actor": self.actor,
            "appliedAt": self.applied_at,
            "ledger": {
                "tenant": self.ledger_tenant,
                "action": self.ledger_action,
                "seq": self.ledger_seq,
                "hash": self.ledger_hash,
            },
            "stream": {"id": self.stream_id, "path": self.stream_path},
            "recovered": self.recovered,
        }
        receipt = self.receipt
        if receipt is not None:
            payload["receipt"] = receipt
        return payload


# ---------------------------------------------------------------------------
# the exactly-once store
# ---------------------------------------------------------------------------
class ControlAudit:
    """The command record path: one effect, one record — and the refusal path.

    Implements the transport's ``CommandLedger`` seam (``begin`` / ``finish`` /
    ``end``) and adds the two readers a client or a console needs: the receipt
    of a command that already applied, and the recovery of the index from the
    rail after a restart.

    Thread-safe by construction: the console serves requests on threads
    (``portal/server/httpd.py`` is a ``ThreadingMixIn`` server), so the
    reservation, the decision and the index update happen under one lock. A
    command is either *in flight* (one reservation, no record yet), *applied*
    (one record, replay refused) — or refused.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str | None = None,
        ledger_dir: Path | str | None = None,
        slog_path: Path | str | None = None,
        tenant: str = DEFAULT_TENANT,
        store: Any = None,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root is not None else REPO_ROOT
        raw_ledger = ledger_dir if ledger_dir is not None else os.environ.get(LEDGER_DIR_ENV, "")
        self.ledger_dir = Path(raw_ledger) if raw_ledger else self.repo_root / DEFAULT_LEDGER_DIR
        fleet_dir = Path(os.environ.get(FLEET_DIR_ENV) or (self.repo_root / DEFAULT_FLEET_DIR))
        raw_slog = slog_path if slog_path is not None else os.environ.get(SLOG_ENV, "")
        self.slog_path = Path(raw_slog) if raw_slog else fleet_dir / SLOG_FILENAME
        if not tenant:
            raise ValueError("the control audit needs the tenant whose chain it records on")
        self.tenant = tenant
        self._store = store
        self._ledger_errors: Optional[tuple[type, ...]] = None
        self._lock = threading.RLock()
        self._in_flight: dict[str, str] = {}
        self._applied: dict[str, RecordedCommand] = {}

    # -- the seam (CommandLedger) -------------------------------------------
    def begin(self, command: CommandLike) -> Optional[str]:
        """Reserve ``command``; the refusal string, or ``None`` to proceed.

        The decision order is the contract: a command id that has *already been
        applied* is refused before it can become a second effect (replay: the
        original receipt is handed back; stolen or reordered: it is not), and a
        command id that is *still in flight* keeps RC-3's ``409`` reservation.
        """
        with self._lock:
            recorded = self._applied.get(command.id)
            if recorded is not None:
                return self._refusal(command, recorded)
            if command.id in self._in_flight:
                return (
                    f"command {command.id!r} for {command.verb} is already in "
                    "flight: it has been accepted and its effect is not yet known"
                )
            self._in_flight[command.id] = command.verb
            return None

    def finish(self, command: CommandLike, record: EffectRecordLike) -> None:
        """Record the effect of one applied command — once, on the existing rails.

        Called by the transport **only** after the lever applied the command; a
        lever that was unreachable or that declined never reaches here, so no
        record is ever written for an effect that did not happen.

        The order of writing is the recovery path: the hash-chained ledger
        record first (the durable authority), then the index entry that makes a
        replay impossible, then the stream line. A failure anywhere is raised,
        never swallowed, and the index entry stands either way — a command that
        an operator retried after a half-written record must not be applied a
        second time.
        """
        with self._lock:
            if command.id in self._applied:
                return
            action = record.audit_action or command.audit_action
            if not action:
                raise AuditUnavailable(
                    f"{command.verb} is a mutating control verb that declares no "
                    "audit action, so its effect cannot be recorded (the registry's "
                    "own rule is that every non-read verb declares one)"
                )
            applied_at = record.requested_at or command.requested_at or utc_now()
            failure = ""
            ledger_row: dict[str, Any] = {}
            try:
                ledger_row = self._append_ledger(command, record, action)
            except AuditUnavailable as exc:
                failure = str(exc)
            recorded = RecordedCommand(
                command_id=command.id,
                record_id=mint_record_id(),
                verb=command.verb,
                actor=record.actor,
                binding=command_binding(command),
                applied_at=applied_at,
                ledger_tenant=self.tenant,
                ledger_action=action,
                ledger_seq=int(ledger_row.get("seq") or 0),
                ledger_hash=str(ledger_row.get("hash") or ""),
                stream_id=mint_record_id(),
                stream_path=str(self.slog_path),
                receipt_json=canonical(record.as_json()),
            )
            self._applied[command.id] = recorded
            if failure:
                raise AuditUnavailable(
                    f"the command applied and the audit record did not ({failure}): "
                    f"the command id is spent, so a retry is refused rather than "
                    f"applied a second time (reported as {command.id!r})"
                )
            try:
                self._append_stream(recorded)
            except OSError as exc:
                raise AuditUnavailable(
                    f"the ledger record exists (seq {recorded.ledger_seq}) and the "
                    f"{self.slog_path} line does not: {exc}"
                ) from exc

    def end(self, command: CommandLike) -> None:
        """Release the reservation taken by :meth:`begin`."""
        with self._lock:
            self._in_flight.pop(command.id, None)

    # -- readers ------------------------------------------------------------
    def receipt_of(self, command_id: str) -> Optional[dict[str, Any]]:
        """The original receipt of an applied command, or ``None``."""
        recorded = self._applied.get(command_id)
        return recorded.receipt if recorded is not None else None

    def record_of(self, command_id: str) -> Optional[RecordedCommand]:
        """The store's own record of an applied command, or ``None``."""
        return self._applied.get(command_id)

    def applied_ids(self) -> tuple[str, ...]:
        """Every command id this store has spent (in the order it spent them)."""
        return tuple(self._applied)

    def rails(self) -> dict[str, Any]:
        """Where this store writes — named, so a receipt can point at it."""
        return {
            "ledgerTenant": self.tenant,
            "ledgerDir": str(self.ledger_dir),
            "streamPath": str(self.slog_path),
        }

    # -- the recovery path --------------------------------------------------
    def recover(self) -> dict[str, Any]:
        """Rebuild the spent-id index from the chain (after a restart).

        The rail is the durable authority, so the index is rebuilt from *it* and
        never from a second file. Only the evidence pointer is read — a chain
        payload would need the tenant's encryption key, which a console holds
        none of — so a recovered record reports its receipt as **unavailable**
        (``recovered: true``, ``receipt: null``) instead of inventing one. The
        property recovery buys is the one that matters: a command the rail
        remembers is refused, never applied a second time.

        Tri-state, like every verdict in this repo: ``OK`` / ``CANNOT-ASSESS``
        with the reason. It never raises — a caller decides what an unreadable
        rail means, and a start-up that cannot read it cannot promise
        exactly-once.
        """
        try:
            rows = self._ledger_store().records(self.tenant)
        except Exception as exc:  # noqa: BLE001 - the verdict is the report
            return {
                "status": "CANNOT-ASSESS",
                "recovered": 0,
                "detail": f"the control audit rail at {self.ledger_dir} is unreadable: {exc}",
            }
        recovered = 0
        with self._lock:
            for row in rows:
                evidence = str(row.get("evidence") or "")
                if not evidence.startswith(EVIDENCE_PREFIX):
                    continue
                command_id = evidence[len(EVIDENCE_PREFIX):]
                if not command_id or command_id in self._applied:
                    continue
                self._applied[command_id] = RecordedCommand(
                    command_id=command_id,
                    record_id=str(row.get("hash") or ""),
                    verb=str(row.get("resource") or "").removeprefix("fleet/control/"),
                    actor=str(row.get("actor") or ""),
                    binding="",
                    applied_at=str(row.get("ts") or ""),
                    ledger_tenant=self.tenant,
                    ledger_action=str(row.get("action") or ""),
                    ledger_seq=int(row.get("seq") or 0),
                    ledger_hash=str(row.get("hash") or ""),
                    stream_id="",
                    stream_path="",
                    recovered=True,
                )
                recovered += 1
        return {
            "status": "OK",
            "recovered": recovered,
            "detail": f"{recovered} applied command(s) read back from {self.ledger_dir}",
        }

    # -- the refusal path ---------------------------------------------------
    def _refusal(self, command: CommandLike, recorded: RecordedCommand) -> str:
        """The refusal for a command id that has already been spent.

        Four cases, and the difference between them is the whole point:

        * same binding — a **replay**. The caller is handed the original receipt,
          verbatim, after :data:`RECEIPT_MARKER`, and the lever is never reached.
        * a different principal — a **stolen** id. Refused *without* the receipt:
          a thief must not be handed someone else's effect.
        * a different order — a **reordered** id. Also refused without the
          receipt: it is not the original command, so it does not get its answer.
        * no binding at all — a record **rebuilt by a restart**, which carries no
          fingerprint of the order that spent the id (see :meth:`recover`). The id
          is spent either way, so it is refused; and because there is no evidence
          of a reorder, calling it one would be a refusal asserting a fact it does
          not hold, so it refuses as *already applied* and reports the receipt
          unavailable rather than reconstructing one.
        """
        if recorded.binding and recorded.binding == command_binding(command):
            return (
                f"command {command.id!r} for {command.verb} was already applied at "
                f"{recorded.applied_at} — a replay is refused rather than applied a "
                f"second time; the original receipt is returned verbatim, "
                f"{RECEIPT_MARKER}{recorded.receipt_json}"
            )
        if recorded.actor != command.actor:
            return (
                f"command id {command.id!r} is bound to {recorded.actor}, and this "
                f"call presents it as {command.actor}: a stolen command id is refused"
            )
        if recorded.binding:
            return (
                f"command id {command.id!r} already carried {recorded.verb} at "
                f"{recorded.applied_at}, and this call reorders it onto "
                f"{command.verb}: a reordered command id is refused"
            )
        return (
            f"command {command.id!r} for {command.verb} was already applied at "
            f"{recorded.applied_at} — the rail remembers it across a restart, so it "
            f"is refused rather than applied a second time; the original receipt is "
            f"unavailable in this process and is not reconstructed, "
            f"{RECEIPT_MARKER}{recorded.receipt_json}"
        )

    # -- the rails ----------------------------------------------------------
    def _ledger_store(self) -> Any:
        """The existing ``telemetry/ledger`` rail, opened once (fail closed).

        Opening is as fallible as appending — a rail path that is a file rather
        than a directory, an unwritable parent, an unreadable key — so it fails
        closed the same way the append does. A raw ``OSError`` escaping into the
        transport would be a crash where the caller was owed a refusal, and
        ``FileExistsError`` is exactly what a misconfigured rail path raises.
        """
        if self._store is None:
            try:
                from telemetry.ledger import errors as ledger_errors
                from telemetry.ledger.store import open_ledger
            except ImportError as exc:  # the rail itself cannot be reached
                raise AuditUnavailable(
                    f"the telemetry/ledger rail is not importable, so no control "
                    f"effect can be recorded: {exc}"
                ) from exc
            errors: tuple[type, ...] = (ledger_errors.LedgerError, OSError)
            try:
                self._store = open_ledger(str(self.ledger_dir))
            except errors as exc:
                raise AuditUnavailable(
                    f"the telemetry/ledger rail at {self.ledger_dir} could not be "
                    f"opened, so no control effect can be recorded: {exc}"
                ) from exc
            self._ledger_errors = errors
        return self._store

    def _append_ledger(
        self, command: CommandLike, record: EffectRecordLike, action: str
    ) -> dict[str, Any]:
        """One hash-chained record on the tenant's chain, through the rail's API.

        ``action`` is the registry's own ``audit`` field for the verb — a lookup,
        never an invention (RC-2 enforces the two-way rule: a read verb declares
        none, every other class declares one). ``evidence`` is the pointer back
        to the command that ordered the effect, which is what
        :meth:`recover` reads to rebuild the spent-id index.
        """
        store = self._ledger_store()
        errors = self._ledger_errors or (OSError,)
        try:
            return store.append(
                self.tenant,
                actor=record.actor,
                action=action,
                resource=f"fleet/control/{record.verb}",
                evidence=f"{EVIDENCE_PREFIX}{command.id}",
                ts=record.requested_at or None,
            )
        except errors as exc:
            raise AuditUnavailable(
                f"the telemetry/ledger rail refused the record for {command.verb}: {exc}"
            ) from exc

    def _append_stream(self, recorded: RecordedCommand) -> None:
        """One line on the fleet's existing ``.fleet/slog.jsonl`` stream.

        The shape is the fleet's own writer's (``fleet/channel.py::_slog``) —
        same keys, same append-and-flock discipline, body bounded the same way —
        because this is the *existing* stream and a second one would be a second
        audit path. ``fleet.channel`` is not imported for it: it imports its lane
        siblings by bare module name (``runtime``, ``governance.policy.lease``),
        which cannot be resolved from inside the console process. A test pins the
        two shapes against each other so they cannot drift apart in silence.
        """
        entry = {
            "ts": recorded.applied_at or utc_now(),
            "id": recorded.stream_id,
            "from": STREAM_FROM,
            "to": STREAM_TO,
            "type": STREAM_TYPE,
            "correlation_id": recorded.command_id,
            "issue": None,
            "severity": "info",
            "body": (
                f"{recorded.verb} applied by {recorded.actor} "
                f"(command {recorded.command_id}, ledger seq {recorded.ledger_seq})"
            )[:BODY_LIMIT],
        }
        path = Path(recorded.stream_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # ``json.dumps``, NOT :func:`canonical`: the fleet's own writer emits this
        # record in declaration order (``fleet/channel.py::_slog`` writes bare
        # ``json.dumps(entry)``), and the shape a peer writer must match is the one
        # that writer actually produces. A sorted payload is the same object with
        # its keys reordered — precisely the drift :func:`record_shape` exists to
        # catch, so sorting here would make this module the thing it is pinned
        # against.
        payload = (json.dumps(entry) + "\n").encode("utf-8")
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            _lock_file(handle)
            os.write(handle, payload)
        finally:
            _unlock_file(handle)
            os.close(handle)


def _lock_file(descriptor: int) -> None:
    """Exclusive lock, where the platform has one (the fleet writer's own step)."""
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except (ImportError, OSError):  # pragma: no cover - non-POSIX hosts
        return


def _unlock_file(descriptor: int) -> None:
    """Release :func:`_lock_file` (a no-op where it could not be taken)."""
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except (ImportError, OSError):  # pragma: no cover - non-POSIX hosts
        return


def record_shape() -> tuple[str, ...]:
    """The keys one stream record carries (the shape a peer writer must match)."""
    return (
        "ts",
        "id",
        "from",
        "to",
        "type",
        "correlation_id",
        "issue",
        "severity",
        "body",
    )


def audit_rails(repo_root: Path | str | None = None) -> Mapping[str, str]:
    """Where the wired surface writes, without building the whole store.

    A caller that needs to *report* the rails (the check script, a console's
    start-up banner) gets the same resolution the collaborator would use.
    """
    store = ControlAudit(repo_root=repo_root)
    return {"ledgerDir": str(store.ledger_dir), "streamPath": str(store.slog_path)}
