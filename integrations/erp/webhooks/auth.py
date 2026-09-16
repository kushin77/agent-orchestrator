"""HMAC signature verification for inbound conversion events (issue #671).

The bridge trusts no payload without a verified signature — verification runs
*before* schema parsing in :mod:`bridge`, so an attacker who can guess the
event shape still cannot get a forged event as far as the ledger. This mirrors
the fail-closed posture ``integrations/erp/auth`` documents for its own
contracts: a signature that cannot be verified is ``auth-failed``, never
"assume valid".

The scheme is a plain ``HMAC-SHA256`` over the raw request body, hex-encoded,
carried in a caller-supplied header value — the same shape most webhook
senders (Stripe, GitHub, and CRM vendors alike) use, chosen because it needs
no shared library beyond :mod:`hmac`/:mod:`hashlib` from the stdlib. This is a
generic HMAC pattern, not anything harvested from ERPNext or any CRM vendor's
source.
"""

from __future__ import annotations

import hmac
import hashlib
from typing import Optional

from .model import Refused

#: The header a sender is expected to carry the signature in. Named once so a
#: transport adapter (not part of this lane) has exactly one contract to honor.
SIGNATURE_HEADER = "X-ERP-Webhook-Signature"

#: The prefix a signature value carries, mirroring the common
#: ``sha256=<hex>`` convention so a raw digest can never be mistaken for a
#: correctly-labeled one.
SIGNATURE_PREFIX = "sha256="


def sign(secret: str, raw_body: bytes) -> str:
    """The signature header value a sender with ``secret`` would send."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify(secret: str, raw_body: bytes, signature_header: Optional[str]) -> None:
    """Raise ``Refused('auth-failed', ...)`` unless ``signature_header`` verifies.

    Fail-closed on every departure from the happy path: a missing header, an
    unlabeled value, a malformed hex digest, and a digest that does not match
    are all ``auth-failed`` — this function never distinguishes "wrong" from
    "unreadable" to a caller, because a bridge that leaks *why* a signature
    failed hands an attacker an oracle.
    """
    if not secret:
        raise Refused("auth-failed", "no webhook secret is configured for this tenant")
    if not signature_header or not isinstance(signature_header, str):
        raise Refused("auth-failed", "no signature header was presented")
    if not signature_header.startswith(SIGNATURE_PREFIX):
        raise Refused("auth-failed", "signature header is not sha256-labeled")
    presented = signature_header[len(SIGNATURE_PREFIX):]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not presented or not hmac.compare_digest(presented, expected):
        raise Refused("auth-failed", "signature does not verify against the configured secret")
