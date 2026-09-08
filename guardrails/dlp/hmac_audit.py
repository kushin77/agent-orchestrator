"""guardrails.dlp.hmac_audit — per-call HMAC signing and tamper-evident audit.

Every outbound commercial-model call carries an HMAC-SHA256 tag bound to the
call record (tenant, agent, call id, provider, endpoint, payload hash,
timestamp, nonce). A signed audit record is append-only and tamper-evident:
any field change invalidates the tag. **No unaudited call** — the egress
pipeline refuses to dispatch until the audit append succeeds and returns the
signature.

Key handling (fail closed):

* the signing key comes from the ``AO_DLP_HMAC_KEY`` environment variable or
  is injected explicitly by the caller (tests inject synthetic keys);
* :class:`HmacSigner` with no key raises :class:`HmacKeyError` — there is no
  embedded/fallback key in production code, so a misconfigured deployment
  cannot silently sign with a known key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

_ENV_KEY = "AO_DLP_HMAC_KEY"
_SIGNATURE_KEYS = frozenset({"hmac", "signature"})
_EXCLUDED_KEYS = _SIGNATURE_KEYS | frozenset({"error"})


class HmacKeyError(RuntimeError):
    """Raised when no HMAC key is available (fail closed)."""


def _canonical_json(record: Mapping[str, Any]) -> str:
    """Deterministic canonical serialization for signing."""
    signing = {k: v for k, v in record.items() if k not in _EXCLUDED_KEYS}
    return json.dumps(signing, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class HmacSigner:
    """HMAC-SHA256 signer/verifier over canonical call records."""

    def __init__(self, key: Optional[bytes] = None) -> None:
        if key is None:
            raw = os.environ.get(_ENV_KEY)
            if raw is None:
                raise HmacKeyError(
                    f"no HMAC key: set {_ENV_KEY} or pass an explicit key (fail closed)"
                )
            key = raw.encode("utf-8")
        if not key:
            raise HmacKeyError("HMAC key is empty (fail closed)")
        self._key = key

    def sign(self, record: Mapping[str, Any]) -> str:
        """Return the hex HMAC-SHA256 over the canonical record."""
        payload = _canonical_json(record).encode("utf-8")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def verify(self, record: Mapping[str, Any], signature: str) -> bool:
        """Return True iff ``signature`` matches the canonical record."""
        computed = self.sign(record)
        return hmac.compare_digest(computed, signature)


@dataclass
class AuditIntegrity:
    """Result of replay-verifying an audit log."""

    total: int
    valid: int
    tampered: list = field(default_factory=list)  # record ids (or line numbers)
    malformed: int = 0

    @property
    def ok(self) -> bool:
        return self.total == self.valid and self.malformed == 0


@dataclass
class HmacAuditLog:
    """Append-only, per-record-signed audit log (JSONL or in-memory)."""

    signer: HmacSigner
    path: Optional[str] = None
    _records: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

    def append(self, record: Mapping[str, Any]) -> dict:
        """Sign and append one record; returns the signed record (with 'hmac')."""
        signed = dict(record)
        if "hmac" in signed:
            raise ValueError("record already carries an 'hmac' field")
        signed["hmac"] = self.signer.sign(signed)
        self._records.append(signed)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(signed, sort_keys=True) + "\n")
        return signed

    def records(self) -> list:
        """All in-memory signed records (not the file replay)."""
        return list(self._records)

    def verify_record(self, record: Mapping[str, Any]) -> bool:
        """Verify a single signed record (True when the tag matches)."""
        sig = record.get("hmac")
        if not isinstance(sig, str):
            return False
        return self.signer.verify(record, sig)

    def verify(self) -> AuditIntegrity:
        """Replay and verify every record (the file is authoritative when set).

        When ``path`` is configured the on-disk JSONL is the record of truth;
        otherwise the in-memory records are verified. This keeps a record from
        being counted twice (appended to memory and then flushed to disk).
        """
        records: list = []
        malformed = 0
        if self.path is not None and os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        malformed += 1
        else:
            records.extend(self._records)

        valid = 0
        tampered: list = []
        for idx, rec in enumerate(records):
            rid = rec.get("record_id") or rec.get("call_id") or f"line-{idx + 1}"
            if self.verify_record(rec):
                valid += 1
            else:
                tampered.append(rid)
        return AuditIntegrity(
            total=len(records),
            valid=valid,
            tampered=tampered,
            malformed=malformed,
        )

    def __len__(self) -> int:
        return len(self._records)
