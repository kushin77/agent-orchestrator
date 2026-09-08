"""Exception hierarchy for telemetry/ledger (issue #31).

Kept in one module so the rest of the package raises typed errors without
import cycles. Every error subclasses :class:`LedgerError`; the security-
sensitive leaves (key missing, cipher unavailable, decryption failure,
repair refused) are distinct so callers can fail closed with intent rather
than by string matching.
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every telemetry/ledger error."""


class LedgerValidationError(LedgerError):
    """A record or append argument violates the audit-event schema."""


class LedgerCorruptError(LedgerError):
    """A ledger file contains unreadable bytes (malformed JSON line, I/O).

    Distinct from a *broken chain*: corruption cannot be attributed to a
    specific tamper, so verification reports it as CANNOT-ASSESS rather than
    the definite NOT-OK of a parseable-but-broken chain.
    """


class LedgerIntegrityError(LedgerError):
    """A parseable ledger violates its integrity contract.

    Raised (fail closed) when a chain is broken or a record violates the
    schema - the definite-tamper counterpart of :class:`LedgerCorruptError`.
    """


class KeyUnavailableError(LedgerError):
    """No encryption key is available for the requested tenant.

    Sensitive payloads must never be stored in plaintext: when a payload is
    supplied but the tenant key cannot be resolved, the append is refused.
    """


class LedgerCryptoError(LedgerError):
    """Base class for encryption-at-rest failures."""


class CipherUnavailableError(LedgerCryptoError):
    """The AES-256-GCM cipher library is not importable.

    The ledger fails closed: without the cipher it refuses to store
    plaintext payloads rather than silently downgrading to plaintext.
    """


class DecryptionError(LedgerCryptoError):
    """A stored envelope could not be decrypted (wrong key, tampered bytes,
    unknown algorithm, or malformed envelope)."""


class RepairRefusedError(LedgerError):
    """A destructive repair (rechaining) was attempted without explicit
    acknowledgement. Rechaining is opt-in and must be acknowledged."""
