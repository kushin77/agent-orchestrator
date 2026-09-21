"""Audit-event schema + canonical serialization (telemetry/ledger, issue #31).

---knowledge---
module_id: telemetry.ledger.schema
system: telemetry
app: ledger
solution_class: enterprise
patterns: [canonical-serialization, single-record-contract, validators]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [build_record, validate_record, canonical_bytes, record_hash, encode_actor, parse_actor, GENESIS_HASH, REQUIRED_FIELDS]
invariants: "one record contract: the canonical JSON byte serialization is the single input to hashing, so on-disk and in-memory records cannot disagree"
gotchas: "camelCase wire keys (tenantId, prevHash) with snake_case python parameters"
related: ["#31", "#1510"]
do_not_duplicate: null
---knowledge---


Defines the single record contract of the tamper-evident audit ledger: the
closed set of fields every stored record carries, the canonical JSON byte
serialization used for hashing, and the validators that keep on-disk and
in-memory records honest. The authoritative human/machine-readable mirror of
this contract is ``audit_event.schema.json`` in this directory.

Wire convention follows the repo (camelCase keys such as ``tenantId``,
``prevHash``; ``ts`` RFC 3339 UTC; python-side snake_case parameters).

Every record that enters the ledger is produced by :func:`build_record` and
checked by :func:`validate_record` before it is chained, so a field that is
not in this contract cannot silently reach the store.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional, Tuple

# The one clock seam (``telemetry/clock.py``, issue #1025).
#
# This package is reachable under TWO import roots, and both are live: the
# canonical one (repo root on ``sys.path`` — ``python3 -m telemetry.ledger.cli``,
# ``telemetry/audit/read_model.py``'s sibling imports) and the flat one
# (``telemetry/`` on ``sys.path`` — ``telemetry/ledger/tests``,
# ``scripts/check-audit-read-model.sh``, which all do ``from ledger import ...``).
# Measured: under the flat root ``import telemetry.clock`` fails, so the seam is
# reached by its second name there.  Both names are the same file, and the seam
# keeps its override in the environment rather than in a module global, so the
# two module objects agree on what time it is.

try:  # canonical root: imported as ``telemetry.ledger.schema``
    from ..clock import now_utc_iso as _now_utc_iso
except ImportError:  # flat root: imported as top-level ``ledger.schema``
    from clock import now_utc_iso as _now_utc_iso

from .errors import LedgerValidationError

# Fixed anchor for the first record of every tenant chain. Not a secret: a
# well-known constant that makes each per-tenant chain start deterministically.
GENESIS_HASH = "0" * 64

# Contract version of this record shape. It is hashed into every record so a
# future schema migration cannot be mixed into an existing chain unnoticed.
SCHEMA_VERSION = 1

# Canonical actor kinds. ``actor`` is stored as ``"kind:id"`` so consumers can
# tell an agent action from a human action at a glance and so the chain
# records *who* (agent/user/system) caused the event.
ACTOR_KINDS = ("user", "agent", "system")

# Canonical JSON serialization used for hashing: deterministic across runs.
_JSON_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}

# RFC 3339 UTC, second or sub-second precision, always Z.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

# A sha256 hex digest.
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Closed field set of a stored record. ``payloadEnc`` is an AES-256-GCM
# envelope or null; clear payloads are never part of the stored contract.
REQUIRED_FIELDS = (
    "schemaVersion",
    "seq",
    "ts",
    "tenantId",
    "actor",
    "action",
    "prevHash",
    "hash",
)
OPTIONAL_FIELDS = (
    "impersonatedBy",
    "resource",
    "evidence",
    "modelUsed",
    "costUsd",
    "payloadEnc",
)


def now_utc() -> str:
    """RFC 3339 UTC timestamp, e.g. ``2026-09-08T12:00:00Z``.

    Delegated to the one clock seam (``telemetry/clock.py``, issue #1025) so the
    ledger's record timestamps can be pinned by a test or a gate like every
    other timestamp on the money path.
    """
    return _now_utc_iso()


def canonical_bytes(record: Dict[str, Any]) -> bytes:
    """Deterministic JSON bytes of ``record`` with the ``hash`` field excluded.

    This is the byte string the chain hashes: it covers every stored field
    (including the encrypted envelope and the link to the previous record) but
    never the record's own ``hash``.
    """
    body = {key: value for key, value in record.items() if key != "hash"}
    return json.dumps(body, **_JSON_KW).encode("utf-8")


def record_hash(record: Dict[str, Any]) -> str:
    """sha256 hex digest of the record's canonical bytes."""
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


def encode_actor(kind: str, actor_id: str) -> str:
    """Canonical ``actor`` string ``"kind:id"`` with a closed kind set."""
    if kind not in ACTOR_KINDS:
        raise LedgerValidationError(
            f"actor kind {kind!r} not in closed set {ACTOR_KINDS}"
        )
    if not isinstance(actor_id, str) or not actor_id:
        raise LedgerValidationError("actor id must be a non-empty string")
    return f"{kind}:{actor_id}"


def parse_actor(actor: str) -> Tuple[str, str]:
    """Split a canonical ``actor`` string into ``(kind, id)``."""
    if not isinstance(actor, str) or ":" not in actor:
        raise LedgerValidationError(
            f"actor {actor!r} must be canonical 'kind:id'"
        )
    kind, _, actor_id = actor.partition(":")
    if kind not in ACTOR_KINDS or not actor_id:
        raise LedgerValidationError(f"actor {actor!r} is not 'kind:id'")
    return kind, actor_id


def _check_optional_string(value: Any, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise LedgerValidationError(f"{field} must be a string or null")


def _check_optional_cost(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, (str, int, float)):
        raise LedgerValidationError("costUsd must be a number, decimal string or null")


def validate_record(
    record: Dict[str, Any],
    *,
    tenant_id: Optional[str] = None,
    seq: Optional[int] = None,
) -> None:
    """Validate one stored record against the audit-event contract.

    When ``tenant_id`` is given the record's ``tenantId`` must match it (this
    is how a cross-tenant record is rejected at the boundary and how the
    per-tenant chain stays isolated). Raises :class:`LedgerValidationError` on
    the first violation.
    """
    if not isinstance(record, dict):
        raise LedgerValidationError("record must be a JSON object")

    for field in REQUIRED_FIELDS:
        if field not in record:
            raise LedgerValidationError(f"record missing required field {field!r}")

    if record["schemaVersion"] != SCHEMA_VERSION:
        raise LedgerValidationError(
            f"unsupported schemaVersion {record['schemaVersion']!r} "
            f"(expected {SCHEMA_VERSION})"
        )

    seq_value = record["seq"]
    if not isinstance(seq_value, int) or isinstance(seq_value, bool) or seq_value < 1:
        raise LedgerValidationError("seq must be an integer >= 1")
    if seq is not None and seq_value != seq:
        raise LedgerValidationError(
            f"seq {seq_value} does not match expected {seq}"
        )

    ts = record["ts"]
    if not isinstance(ts, str) or not _TS_RE.match(ts):
        raise LedgerValidationError(f"ts {ts!r} is not RFC 3339 UTC")

    tenant = record["tenantId"]
    if not isinstance(tenant, str) or not tenant:
        raise LedgerValidationError("tenantId must be a non-empty string")
    if tenant_id is not None and tenant != tenant_id:
        raise LedgerValidationError(
            f"record tenantId {tenant!r} does not match chain tenant {tenant_id!r}"
        )

    parse_actor(record["actor"])  # raises on malformed actor

    action = record["action"]
    if not isinstance(action, str) or not action:
        raise LedgerValidationError("action must be a non-empty string")

    for field in ("prevHash", "hash"):
        value = record[field]
        if not isinstance(value, str) or not _HEX64_RE.match(value):
            raise LedgerValidationError(f"{field} must be a 64-char sha256 hex digest")

    _check_optional_string(record.get("impersonatedBy"), "impersonatedBy")
    _check_optional_string(record.get("resource"), "resource")
    _check_optional_string(record.get("evidence"), "evidence")
    _check_optional_string(record.get("modelUsed"), "modelUsed")
    _check_optional_cost(record.get("costUsd"))

    payload_enc = record.get("payloadEnc")
    if payload_enc is not None and not isinstance(payload_enc, dict):
        raise LedgerValidationError("payloadEnc must be an object or null")


def build_record(
    *,
    tenant_id: str,
    seq: int,
    ts: str,
    prev_hash: str,
    actor: str,
    action: str,
    impersonated_by: Optional[str] = None,
    resource: Optional[str] = None,
    evidence: Optional[str] = None,
    model_used: Optional[str] = None,
    cost_usd: Any = None,
    payload_enc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build, validate and hash one stored audit record.

    Only fields in the contract are accepted (unknown keywords raise
    TypeError). The caller supplies ``payload_enc`` - an already-encrypted
    envelope - never a clear payload, so plaintext cannot reach the store by
    construction.
    """
    if not isinstance(tenant_id, str) or not tenant_id:
        raise LedgerValidationError("tenantId must be a non-empty string")
    parse_actor(actor)

    record: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
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
    record["hash"] = record_hash(record)
    validate_record(record, tenant_id=tenant_id, seq=seq)
    return record
