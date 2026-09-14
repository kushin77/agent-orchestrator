"""Typed shapes for the per-agent secret vault primitive (issue #417).

Three shapes live here, deliberately kept apart:

* :class:`SecretRef` — **our** declaration of a secret: the GSM path it lives
  at, the agent and scope it belongs to, when it was last rotated and which
  agent consumes it. It is a *name*, never a value;
* :class:`SecretView` — the emitted reference/rotation view record (the exact
  shape ``schema/secret.schema.json`` validates, so the two cannot drift);
* :class:`Caller` — the authenticated, scoped caller that a read requires
  (``identity/`` remains the authority that mints that identity).

The refusals are typed so a caller can act on them, and so the gate can prove
each one fires. Every message names the *rule* and the *location* — never a
matched value (GR-6).

Stdlib-only by construction (frozen dataclasses + ``typing``), so neither the
tests nor the gate pull a third-party dependency in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

#: The **one** store of record (GR-6). Every declaration and every view names
#: this store; anything else is refused, because two secret stores means two
#: answers to "what is the credential" — the half-coupling ADR-0012 forbids.
GSM_STORE = "google-secret-manager"

#: The platform-wide read scope, granted to a secret administrator. It is a
#: *scope*, so a caller holding it is still a scoped caller, not an unscoped one.
SECRET_WILDCARD = "secret:*"

#: The GSM path shape: ``projects/<project>/secrets/<name>``. Requiring the
#: shape is what makes the path a *reference into the store of record* rather
#: than an arbitrary string that only looks like one.
GSM_PATH_PATTERN = r"^projects/[^/]+/secrets/[^/]+$"

#: Keys that would carry a secret **value** rather than name one. Their presence
#: anywhere in a declaration or a view is refused by name: the value is never
#: read, echoed, logged, or included in a finding (GR-6).
VALUE_KEYS: FrozenSet[str] = frozenset(
    {
        "value",
        "secret",
        "secret_value",
        "secretvalue",
        "plaintext",
        "cleartext",
        "cleartext_value",
        "credential",
        "material",
        "contents",
        "payload",
    }
)


# --------------------------------------------------------------------------
# Errors — each one states its rule, and names no value
# --------------------------------------------------------------------------


class SecretError(Exception):
    """Base error for the secret vault primitive."""


class ValidationError(SecretError):
    """A declaration or view does not conform to the frozen view schema."""


class ValueNotPermittedError(SecretError):
    """A value was carried where a name belongs (GR-6).

    Raised **before** any value is read. The message names the forbidden *key*
    and its location — never the content.
    """


class SecondStoreError(SecretError):
    """A store other than the store of record was named (a second store)."""


class UnscopedReadError(SecretError):
    """A read was attempted by a caller that is not authenticated and scoped."""


class OrphanedSecretError(SecretError):
    """A read was attempted against a secret that has no consumer."""


class UnknownSecretError(SecretError):
    """A read was attempted against a GSM path the view does not declare."""


# --------------------------------------------------------------------------
# Declarations and views
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretRef:
    """Our declaration of one secret: a name, a scope, and its rotation state.

    ``agent`` is the vault the secret belongs to (the scope identity);
    ``consumer`` is the workload that reads it. A secret with no ``consumer`` is
    **orphaned** and is reported, never silently kept.

    There is no ``value`` field, by construction: the shape cannot carry one.
    """

    gsm_path: str
    agent: str
    scope: str
    consumer: Optional[str] = None
    last_rotated_at: Optional[str] = None
    store: str = GSM_STORE

    @property
    def rotated(self) -> bool:
        """True when the view records a rotation time."""
        return bool(self.last_rotated_at)

    @property
    def orphaned(self) -> bool:
        """True when no consumer claims this secret."""
        return not (self.consumer or "").strip()

    def to_view(self) -> Dict[str, Any]:
        """The reference/rotation view record — the shape the schema validates."""
        return {
            "gsm_path": self.gsm_path,
            "agent": self.agent,
            "scope": self.scope,
            "store": self.store,
            "consumer": self.consumer,
            "last_rotated_at": self.last_rotated_at,
            "rotated": self.rotated,
        }


#: The keys a declaration must carry to become a :class:`SecretRef`. The
#: projection copies only these, so a declaration that carries a value can never
#: leak it into a view.
_NAME_KEYS: Tuple[str, ...] = (
    "gsm_path",
    "agent",
    "scope",
    "consumer",
    "last_rotated_at",
    "store",
)


def ref_from_declaration(declaration: Dict[str, Any]) -> SecretRef:
    """Project a raw declaration onto :class:`SecretRef`.

    Only :data:`_NAME_KEYS` are read. A value-bearing key in the declaration is
    ignored here and refused by :func:`vault.value_findings`; either way it never
    reaches a view.
    """
    gsm_path = str(declaration.get("gsm_path") or "")
    store = declaration.get("store") or GSM_STORE
    consumer = declaration.get("consumer")
    rotated_at = declaration.get("last_rotated_at")
    return SecretRef(
        gsm_path=gsm_path,
        agent=str(declaration.get("agent") or ""),
        scope=str(declaration.get("scope") or ""),
        consumer=None if consumer is None else str(consumer),
        last_rotated_at=None if rotated_at is None else str(rotated_at),
        store=str(store),
    )


@dataclass(frozen=True)
class Caller:
    """The authenticated, scoped caller a read requires.

    ``identity/`` (the front door + RBAC/entitlements) is the authority that
    authenticates the session and resolves its scopes; this shape is the
    *consumed* result, exactly as the control plane consumes an already-verified
    principal. An empty ``principal`` is an unauthenticated caller; an empty
    ``scopes`` (or one that does not cover the secret's scope) is an unscoped
    caller. Both are refused.
    """

    principal: str = ""
    scopes: FrozenSet[str] = frozenset()

    @classmethod
    def of(cls, principal: str, scopes: Iterable[str] = ()) -> "Caller":
        return cls(principal=principal, scopes=frozenset(scopes))

    @property
    def authenticated(self) -> bool:
        """True when the caller carries an identity."""
        return bool((self.principal or "").strip())

    def may_read(self, scope: str) -> bool:
        """True when the caller is scoped for ``scope``.

        Least privilege: the secret's own scope, or the platform-wide
        :data:`SECRET_WILDCARD` scope, and nothing else.
        """
        return bool(scope) and (scope in self.scopes or SECRET_WILDCARD in self.scopes)


@dataclass(frozen=True)
class SecretView:
    """The emitted view document: one store of record, many named secrets."""

    store: str
    secrets: Tuple[Dict[str, Any], ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"store": self.store, "secrets": [dict(s) for s in self.secrets]}
