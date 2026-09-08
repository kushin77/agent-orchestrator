"""Public verification API for telemetry/ledger (issue #31).

Two honest, tri-state surfaces:

* :func:`verify_ledger` / :func:`verify_all` - chain integrity, returning
  :class:`~ledger.store.LedgerVerdict` (OK / NOT-OK / CANNOT-ASSESS). A tamper
  is NEVER a silent pass.
* :func:`read_payload` - decrypt one stored envelope; when the tenant key is
  unavailable or decryption fails the caller gets a CANNOT-ASSESS outcome, not
  a partial answer.

``verdict_exit_code`` maps a verdict to the wire contract shared with the
repo's honesty model (issue #28): OK=0, NOT-OK=1, CANNOT-ASSESS=2, so a CLI or
gate consuming the ledger gets a real, fail-closed exit code.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .crypto import decrypt_payload
from .errors import KeyUnavailableError, LedgerError
from .keystore import Keystore
from .store import LedgerStore, LedgerVerdict


def verify_ledger(
    store: LedgerStore,
    tenant_id: str,
    *,
    expected: Optional[Tuple[int, str]] = None,
) -> LedgerVerdict:
    """Verify one tenant's hash chain end-to-end (tri-state).

    See :meth:`LedgerStore.verify` for the exact semantics. This is the
    canonical entry point for external callers and gates.
    """
    return store.verify(tenant_id, expected=expected)


def verify_all(store: LedgerStore) -> Dict[str, LedgerVerdict]:
    """Verify every tenant chain in the store; one verdict per tenant."""
    return {
        tenant_id: store.verify(tenant_id)
        for tenant_id in store.tenant_ids()
    }


def _decrypt_envelope(
    store: LedgerStore,
    tenant_id: str,
    envelope: Dict[str, Any],
) -> Tuple[bool, Any, str]:
    """Decrypt ``envelope`` returning ``(ok, payload, detail)``.

    Never raises for key/decryption problems: those are reported as
    not-ok with a detail string so callers map them to a tri-state verdict.
    """
    keystore: Optional[Keystore] = store.keystore
    if keystore is None:
        return False, None, "no keystore configured for tenant"
    try:
        material = keystore.get(tenant_id)
    except KeyUnavailableError as exc:
        return False, None, f"CANNOT decrypt payload: {exc}"
    try:
        raw = decrypt_payload(envelope, material.key)
    except LedgerError as exc:
        return False, None, f"CANNOT decrypt payload: {exc}"
    return True, raw, ""


def read_payload(
    store: LedgerStore,
    tenant_id: str,
    seq: int,
) -> Tuple[str, Any, str]:
    """Decrypt and return the payload of record ``seq`` (tri-state outcome).

    Returns ``(status, payload, detail)`` where ``status`` is ``"OK"`` (the
    record has no payload, or it decrypted), ``"CANNOT-ASSESS"`` (the tenant
    key or cipher is unavailable, so the payload cannot be assessed - never a
    silent pass), or ``"NOT-OK"`` (the record is not in the chain at ``seq``).
    The decrypted payload is the original JSON value stored by the producer.
    """
    record: Optional[Dict[str, Any]] = None
    for candidate in store.records(tenant_id):
        if candidate["seq"] == seq:
            record = candidate
            break
    if record is None:
        return "NOT-OK", None, f"no record at seq {seq} in tenant {tenant_id!r}"
    envelope = record.get("payloadEnc")
    if envelope is None:
        return "OK", None, "record has no encrypted payload"
    ok, value, detail = _decrypt_envelope(store, tenant_id, envelope)
    if not ok:
        return "CANNOT-ASSESS", None, detail
    # The envelope sealed canonical_bytes({"payload": <value>}); restore value.
    import json as _json

    try:
        restored = _json.loads(value.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "CANNOT-ASSESS", None, "decrypted payload is not valid JSON"
    return "OK", restored.get("payload"), ""


def verdict_exit_code(verdict: LedgerVerdict) -> int:
    """Map a verdict to the honesty wire contract: OK=0, NOT-OK=1, CANNOT-ASSESS=2."""
    return verdict.exit_code


def all_ok(verdicts: Dict[str, LedgerVerdict]) -> bool:
    """True only when every verdict is OK (CANNOT-ASSESS is never a pass)."""
    return bool(verdicts) and all(v.is_pass for v in verdicts.values())
