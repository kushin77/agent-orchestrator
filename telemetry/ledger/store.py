"""Per-tenant append-only, tamper-evident audit ledger (telemetry/ledger,
issue #31).

This is the evidence layer of the control plane (AO-GR-17): every agent action
and policy decision is recorded on a per-tenant, append-only, hash-chained
ledger whose sensitive payloads are encrypted at rest. One record per action.

Guarantees (each with a negative test in ``tests/``):

* **Append-only.** A record is never rewritten, deleted or reordered; each new
  record chains to the previous one with ``prevHash``.
* **Hash-chained.** ``hash = sha256(canonical JSON of every stored field except
  hash)`` and ``prevHash`` of record *n* equals ``hash`` of record *n-1* (the
  first record anchors to a fixed genesis hash). Any edit, deletion,
  reordering, or cross-tenant insertion breaks ``verify``.
* **Encrypted payloads at rest.** A sensitive payload is AES-256-GCM encrypted
  (see ``crypto.py``) into ``payloadEnc`` before it is stored and hashed; the
  clear payload is never persisted. Appending a payload without a tenant key
  or without the cipher library FAILS CLOSED (nothing is written).
* **Per-tenant isolation.** Each tenant owns a physically separate JSON Lines
  file whose records are all bound to that tenant; the store only ever reads
  or appends the requested tenant's chain, and a foreign-tenant record inside
  a chain is a detected integrity violation.

Storage layout (mirrors ``registry/events``, the CMR append-only pattern):
one JSON object per line in ``<directory>/<tenant>.jsonl``, prefixed by
comment lines (``#``). Every read re-parses and re-verifies the file, so an
external edit is always observed.

Verify tri-state (issue #28 vocabulary): ``OK`` / ``NOT-OK`` /
``CANNOT-ASSESS`` - a parseable-but-broken chain is a definite ``NOT-OK``
(never a silent pass); an unreadable/unparseable file is ``CANNOT-ASSESS``
because the ledger cannot form an honest verdict.

Trailing-truncation note: silently dropping the *last* records cannot be seen
from a file that is otherwise internally consistent - pass the trusted tail
state (previously returned by :meth:`LedgerStore.tail_state`) to
:meth:`LedgerStore.verify` as ``expected`` to detect it.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .crypto import encrypt_payload
from .errors import (
    KeyUnavailableError,
    LedgerCorruptError,
    LedgerIntegrityError,
    LedgerValidationError,
    RepairRefusedError,
)
from .keystore import Keystore
from .schema import (
    GENESIS_HASH,
    canonical_bytes,
    encode_actor,
    now_utc,
    parse_actor,
    record_hash,
    validate_record,
)

# Canonical JSON serialization for stored lines (matches schema.canonical_bytes
# for hashing; sort_keys keeps lines tidy).
_JSON_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}

# File extension for one tenant's chain file.
_EXT = ".jsonl"


def _header(tenant_id: str) -> str:
    return (
        "# telemetry/ledger audit trail - append-only per-tenant hash chain\n"
        "# Tenant: " + tenant_id + "\n"
        "# Schema: telemetry/ledger/audit_event.schema.json\n"
        "# Sensitive payloads are AES-256-GCM encrypted at rest (never plaintext).\n"
    )


# --------------------------------------------------------------------------- #
# Verify verdict
# --------------------------------------------------------------------------- #
class LedgerVerdict:
    """Honest tri-state result of a ledger verification (issue #28 vocabulary).

    ``status`` is one of ``"OK"`` / ``"NOT-OK"`` / ``"CANNOT-ASSESS"``.
    ``NOT-OK`` is a definite tamper (never a silent pass); ``CANNOT-ASSESS``
    means the ledger could not form a verdict (e.g. an unparseable file).
    """

    __slots__ = ("status", "tenant", "tail", "detail", "broken_at")

    OK = "OK"
    NOT_OK = "NOT-OK"
    CANNOT_ASSESS = "CANNOT-ASSESS"

    def __init__(
        self,
        status: str,
        *,
        tenant: str,
        tail: Tuple[int, str],
        detail: str = "",
        broken_at: Optional[int] = None,
    ) -> None:
        self.status = status
        self.tenant = tenant
        self.tail = tail
        self.detail = detail
        self.broken_at = broken_at

    # -- factory helpers -------------------------------------------------- #
    @classmethod
    def ok(cls, tenant: str, tail: Tuple[int, str]) -> "LedgerVerdict":
        return cls(cls.OK, tenant=tenant, tail=tail, detail="chain intact")

    @classmethod
    def not_ok(
        cls,
        tenant: str,
        tail: Tuple[int, str],
        detail: str,
        broken_at: Optional[int] = None,
    ) -> "LedgerVerdict":
        return cls(
            cls.NOT_OK, tenant=tenant, tail=tail, detail=detail, broken_at=broken_at
        )

    @classmethod
    def cannot_assess(cls, tenant: str, detail: str) -> "LedgerVerdict":
        return cls(
            cls.CANNOT_ASSESS, tenant=tenant, tail=(0, GENESIS_HASH), detail=detail
        )

    # -- query helpers ---------------------------------------------------- #
    @property
    def is_pass(self) -> bool:
        """True only for OK - CANNOT-ASSESS is never a pass."""
        return self.status == self.OK

    @property
    def is_fail(self) -> bool:
        """True only for NOT-OK."""
        return self.status == self.NOT_OK

    @property
    def is_unknown(self) -> bool:
        """True only for CANNOT-ASSESS."""
        return self.status == self.CANNOT_ASSESS

    @property
    def exit_code(self) -> int:
        """Wire format for CLI/gate callers: OK=0, NOT-OK=1, CANNOT-ASSESS=2."""
        return {self.OK: 0, self.NOT_OK: 1, self.CANNOT_ASSESS: 2}[self.status]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "tenant": self.tenant,
            "seq": self.tail[0],
            "hash": self.tail[1],
            "detail": self.detail,
            "brokenAt": self.broken_at,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"LedgerVerdict({self.status}, tenant={self.tenant!r}, "
            f"tail={self.tail}, broken_at={self.broken_at}, detail={self.detail!r})"
        )


# --------------------------------------------------------------------------- #
# Ledger store
# --------------------------------------------------------------------------- #
class LedgerStore:
    """Multi-tenant append-only hash-chained audit ledger.

    ``directory`` (optional) enables file backing - one ``<tenant>.jsonl`` per
    tenant; every read re-parses the file so external edits are observed.
    ``keystore`` (optional) resolves per-tenant encryption keys for sensitive
    payloads; appending a payload with no keystore/key/cipher fails closed.
    """

    def __init__(
        self,
        directory: Optional[str] = None,
        keystore: Optional[Keystore] = None,
    ) -> None:
        self._directory = directory
        self._keystore = keystore
        # In-memory chains are used only when directory is None.
        self._memory: Dict[str, List[Dict[str, Any]]] = {}
        if directory is not None:
            os.makedirs(directory, exist_ok=True)

    # ------------------------------------------------------------------ #
    # properties + tenant enumeration
    # ------------------------------------------------------------------ #
    @property
    def directory(self) -> Optional[str]:
        """The ledger directory, or None for a purely in-memory store."""
        return self._directory

    @property
    def keystore(self) -> Optional[Keystore]:
        """The configured key store (may be None when no payloads are used)."""
        return self._keystore

    def _tenant_path(self, tenant_id: str) -> str:
        if self._directory is None:
            raise LedgerIntegrityError("in-memory store has no tenant file path")
        return os.path.join(self._directory, tenant_id + _EXT)

    def tenant_ids(self) -> List[str]:
        """Tenants that have a chain (file-backed or in-memory), sorted."""
        if self._directory is None:
            return sorted(self._memory.keys())
        names: List[str] = []
        if os.path.isdir(self._directory):
            for name in sorted(os.listdir(self._directory)):
                if name.endswith(_EXT):
                    names.append(name[: -len(_EXT)])
        return names

    # ------------------------------------------------------------------ #
    # low-level parsing / inspection
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_lines(path: str) -> List[Dict[str, Any]]:
        """Parse a JSON Lines chain file into record dicts.

        Raises :class:`LedgerCorruptError` on a line that is not valid JSON or
        on an I/O failure - the file cannot be attributed to a specific tamper.
        """
        records: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    try:
                        parsed = json.loads(stripped)
                    except ValueError as exc:
                        raise LedgerCorruptError(
                            f"malformed record line {line_number} in {path}: {exc}"
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise LedgerCorruptError(
                            f"line {line_number} in {path} is not a JSON object"
                        )
                    records.append(parsed)
        except OSError as exc:
            raise LedgerCorruptError(f"cannot read ledger file {path}: {exc}") from exc
        return records

    @staticmethod
    def _inspect(
        records: List[Dict[str, Any]], tenant_id: str
    ) -> Tuple[Optional[Tuple[int, str]], Tuple[int, str]]:
        """Full integrity inspection of one tenant's records.

        Returns ``(problem, tail)``. ``problem`` is ``None`` when the chain is
        intact, else ``(seq, reason)`` for the FIRST violation. ``tail`` is the
        recomputed ``(last_seq, last_hash)`` and is only meaningful when there
        is no problem.
        """
        previous = GENESIS_HASH
        for index, record in enumerate(records, start=1):
            try:
                validate_record(record, tenant_id=tenant_id, seq=index)
            except LedgerValidationError as exc:
                return (index, f"record {index} violates schema: {exc}"), (
                    index - 1,
                    previous,
                )
            if record["tenantId"] != tenant_id:
                return (
                    index,
                    f"record {index} carries foreign tenantId {record['tenantId']!r} "
                    f"inside tenant {tenant_id!r} chain",
                ), (index - 1, previous)
            if record["prevHash"] != previous:
                return (
                    index,
                    f"broken hash link at record {index} (prevHash mismatch)",
                ), (index - 1, previous)
            if record["hash"] != record_hash(record):
                return (
                    index,
                    f"hash mismatch at record {index} (content was altered)",
                ), (index - 1, previous)
            previous = record["hash"]
        return None, (len(records), previous)

    def _read_records(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Strict read of a tenant's current records (fail closed).

        Raises :class:`LedgerCorruptError` for unparseable bytes and
        :class:`LedgerIntegrityError` for a parseable-but-broken chain, so
        callers never operate on an untrusted trail.
        """
        if self._directory is None:
            records = [dict(record) for record in self._memory.get(tenant_id, [])]
            problem, _ = self._inspect(records, tenant_id)
            if problem is not None:
                raise LedgerIntegrityError(problem[1])
            return records
        path = self._tenant_path(tenant_id)
        if not os.path.exists(path):
            return []
        records = self._parse_lines(path)
        problem, _ = self._inspect(records, tenant_id)
        if problem is not None:
            raise LedgerIntegrityError(problem[1])
        return records

    def _read_content(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Read records accepting broken links (used by repair only).

        Content (schema + tenant binding) must still be valid; only the
        ``prevHash``/``hash`` links may be broken, because rechaining exists to
        rebuild exactly those after an independent content check.
        """
        if self._directory is None:
            return [dict(record) for record in self._memory.get(tenant_id, [])]
        path = self._tenant_path(tenant_id)
        if not os.path.exists(path):
            return []
        records = self._parse_lines(path)
        for index, record in enumerate(records, start=1):
            try:
                validate_record(record, tenant_id=tenant_id, seq=index)
            except LedgerValidationError as exc:
                raise LedgerIntegrityError(
                    f"record {index} violates schema (cannot rechain broken "
                    f"content): {exc}"
                ) from exc
        return records

    def _append_line(self, tenant_id: str, record: Dict[str, Any]) -> None:
        if self._directory is None:
            self._memory.setdefault(tenant_id, []).append(dict(record))
            return
        path = self._tenant_path(tenant_id)
        if not os.path.exists(path):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(_header(tenant_id))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, **_JSON_KW) + "\n")

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def append(
        self,
        tenant_id: str,
        *,
        actor: Optional[str] = None,
        action: str,
        actor_kind: Optional[str] = None,
        actor_id: Optional[str] = None,
        impersonated_by: Optional[str] = None,
        resource: Optional[str] = None,
        evidence: Optional[str] = None,
        model_used: Optional[str] = None,
        cost_usd: Any = None,
        payload: Any = None,
        ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append one audit record to the tenant's chain and return it.

        ``actor`` may be a canonical ``"kind:id"`` string or ``actor_kind`` +
        ``actor_id`` (kind in ``user``/``agent``/``system``). ``payload`` is
        the sensitive body: when non-null it is encrypted at rest (never
        stored in plaintext) and a missing key/cipher refuses the append (fail
        closed - nothing is written). ``ts`` defaults to now (RFC 3339 UTC).
        The returned dict is exactly the stored record.
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            raise LedgerValidationError("tenantId must be a non-empty string")
        if actor is None:
            if actor_id is None:
                raise LedgerValidationError("actor (or actor_kind+actor_id) is required")
            actor = encode_actor(actor_kind or "", actor_id)
        parse_actor(actor)  # validates canonical form + closed kind set
        if not isinstance(action, str) or not action:
            raise LedgerValidationError("action must be a non-empty string")
        ts = ts or now_utc()
        if impersonated_by is not None:
            parse_actor(impersonated_by)  # stamps must be canonical too

        # --- encrypt the sensitive payload (fail closed) ------------------ #
        payload_enc: Optional[Dict[str, Any]] = None
        if payload is not None:
            if self._keystore is None:
                raise KeyUnavailableError(
                    f"no keystore configured; refusing to store a plaintext "
                    f"payload for tenant {tenant_id!r}"
                )
            material = self._keystore.get(tenant_id)  # raises KeyUnavailableError
            plaintext = canonical_bytes({"payload": payload})
            payload_enc = encrypt_payload(plaintext, material.key, material.key_id)

        # --- chain the record (strict read, fail closed) ------------------- #
        records = self._read_records(tenant_id)
        seq = len(records) + 1
        prev_hash = records[-1]["hash"] if records else GENESIS_HASH
        record: Dict[str, Any] = {
            "schemaVersion": 1,
            "seq": seq,
            "ts": ts,
            "tenantId": tenant_id,
            "actor": actor,
            "impersonatedBy": impersonated_by,
            "action": action,
            "resource": resource,
            "evidence": evidence,
            "modelUsed": model_used,
            "costUsd": cost_usd,
            "payloadEnc": payload_enc,
            "prevHash": prev_hash,
        }
        # Hash is a pure function of the other fields; compute it first so the
        # stored record passes full contract validation (hash is required).
        record["hash"] = record_hash(record)
        validate_record(record, tenant_id=tenant_id, seq=seq)
        self._append_line(tenant_id, record)
        return dict(record)

    def records(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Snapshot of a tenant's current stored records in chain order.

        Fail closed: a malformed or chain-broken file raises
        :class:`LedgerCorruptError`/:class:`LedgerIntegrityError` rather than
        returning partial data. Always re-reads, so external edits surface.
        """
        return [dict(record) for record in self._read_records(tenant_id)]

    def export(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Compliance export of a tenant's full audit trail (stored form).

        Payloads remain encrypted envelopes in the export; the tenant (which
        holds its key) decrypts them out-of-band. This is the per-tenant
        query/export surface - only the requested tenant's chain is returned.
        """
        return self.records(tenant_id)

    def __len__(self) -> int:
        return sum(len(self.records(tenant)) for tenant in self.tenant_ids())

    def tail_state(self, tenant_id: str) -> Tuple[int, str]:
        """Trusted verified tail ``(last_seq, last_hash)`` of the tenant chain.

        Genesis ``(0, GENESIS_HASH)`` when the tenant has no records yet. Only
        returns a state that passed the full chain walk (fail closed), so it is
        safe to use as the ``expected`` anchor for truncation detection.
        """
        records = self._read_records(tenant_id)
        if not records:
            return (0, GENESIS_HASH)
        return (records[-1]["seq"], records[-1]["hash"])

    def verify(
        self,
        tenant_id: str,
        *,
        expected: Optional[Tuple[int, str]] = None,
    ) -> LedgerVerdict:
        """Verify the tenant's hash chain end-to-end (tri-state, never silent).

        * OK - every link, hash, sequence and tenant binding is intact.
        * NOT-OK - a definite integrity break (edit / delete / reorder /
          cross-tenant insertion or, with ``expected``, truncation / extension).
        * CANNOT-ASSESS - the file cannot be parsed, so no honest verdict.
        """
        if self._directory is not None:
            path = self._tenant_path(tenant_id)
            if not os.path.exists(path):
                # No chain yet. Against a non-genesis expectation this is a
                # definite loss of the whole trail (truncation to zero).
                if expected is not None and expected != (0, GENESIS_HASH):
                    return LedgerVerdict.not_ok(
                        tenant_id,
                        (0, GENESIS_HASH),
                        f"tenant {tenant_id!r} has no chain but expected tail "
                        f"{expected} was provided (whole trail missing)",
                    )
                return LedgerVerdict.ok(tenant_id, (0, GENESIS_HASH))
        try:
            if self._directory is None:
                records: Optional[List[Dict[str, Any]]] = [
                    dict(r) for r in self._memory.get(tenant_id, [])
                ]
            else:
                records = self._parse_lines(path)
        except LedgerCorruptError as exc:
            return LedgerVerdict.cannot_assess(tenant_id, str(exc))
        assert records is not None
        problem, tail = self._inspect(records, tenant_id)
        if problem is not None:
            seq, reason = problem
            return LedgerVerdict.not_ok(tenant_id, tail, reason, broken_at=seq)
        if expected is not None and tail != expected:
            return LedgerVerdict.not_ok(
                tenant_id,
                tail,
                f"chain tail {tail} does not match expected {expected} "
                "(trailing records truncated or chain unexpectedly extended)",
            )
        return LedgerVerdict.ok(tenant_id, tail)

    def rechain(
        self,
        tenant_id: str,
        *,
        acknowledge: bool = False,
    ) -> LedgerVerdict:
        """Explicit, opt-in repair: rebuild a tenant chain's hash links.

        Rechaining recomputes ``seq``/``prevHash``/``hash`` over the CURRENT
        stored content of every record, preserving every other field
        (including the encrypted envelopes). It is the documented recovery
        path used only AFTER an independent check established that the record
        *content* is authoritative - e.g. the file was restored from a trusted
        replica/backup, or envelopes were replaced by a trusted key-rotation
        step. It never blesses content on its own authority and refuses to run
        without ``acknowledge=True`` (:class:`RepairRefusedError`).

        Rechaining rewrites the tenant file; it is destructive to the old
        links by design and is logged by the caller. Normal operation is
        strictly append-only - this is a last-resort, opt-in repair.
        """
        if not acknowledge:
            raise RepairRefusedError(
                "rechain refused: destructive repair requires acknowledge=True "
                "after an independent check that record content is authoritative"
            )
        if self._directory is not None:
            path = self._tenant_path(tenant_id)
            if not os.path.exists(path):
                return LedgerVerdict.ok(tenant_id, (0, GENESIS_HASH))
        content = self._read_content(tenant_id)
        rebuilt: List[Dict[str, Any]] = []
        previous = GENESIS_HASH
        for index, record in enumerate(content, start=1):
            # Content is authoritative here; only link fields are recomputed.
            rebuilt_record: Dict[str, Any] = {
                key: value for key, value in record.items() if key != "hash"
            }
            rebuilt_record["seq"] = index
            rebuilt_record["prevHash"] = previous
            rebuilt_record["hash"] = record_hash(rebuilt_record)
            previous = rebuilt_record["hash"]
            rebuilt.append(rebuilt_record)
        if self._directory is not None:
            path = self._tenant_path(tenant_id)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(_header(tenant_id))
                for record in rebuilt:
                    handle.write(json.dumps(record, **_JSON_KW) + "\n")
        else:
            self._memory[tenant_id] = [dict(record) for record in rebuilt]
        return LedgerVerdict.ok(tenant_id, (len(rebuilt), previous))


def open_ledger(
    directory: Optional[str] = None, keystore: Optional[Keystore] = None
) -> LedgerStore:
    """Open a :class:`LedgerStore`, creating the directory if needed."""
    return LedgerStore(directory=directory, keystore=keystore)
