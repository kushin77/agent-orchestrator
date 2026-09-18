"""The ERP auth model (issue #653, EPIC #645, ERP-08).

This is the model half of the ERP module's **tenant access** lane: the closed
vocabularies (ERP actions, refusal codes), the value types a request travels
in (:class:`Principal`, :class:`Request`, :class:`Decision`), and the refusal
type every other module in this package raises.

**Why the refusal vocabulary is closed.** ``REFUSALS`` is the complete set of
machine-readable reasons this package can refuse for, and :class:`Refused`
refuses to carry a code outside it. A refusal that invents a code is a refusal
a caller cannot branch on and a gate cannot assert, so it is a bug in the
refuser rather than a condition of the input, and it fails here, at the raise,
rather than silently reaching a consumer. ``negative_control.py`` provokes
every code in the set and ``tests/test_negative_control.py`` fails when the
provoked set and this set diverge, so a new refusal cannot ship without a
control that demonstrates it refusing.

**No secrets, by construction (GR-6).** A :class:`Principal` carries *references*
 — a tenant id, a subject id, and role names. It carries no token, no
password and no credential; that is not a convention here but a property of the
type, because there is no field to put one in. The suite asserts this against
the dataclass fields themselves, so a later change that adds a ``token`` field
fails the suite rather than shipping.

**Where the tenant comes from.** ``Request.tenant`` is a *claim*, and
``Principal.tenant`` is the authority. They are separate fields precisely so
that a request can disagree with the principal and be refused for it: see
``scope.authorize``, where the tenant gate runs before any role or policy is
consulted and no declaration can influence it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

#: The envelope version every declaration in this module carries.
SCHEMA_VERSION = 1

# --- the ERP action vocabulary ----------------------------------------------

#: The action names are ERPNext's own permission types, kept verbatim as a
#: closed tuple rather than paraphrase: an operator reading a Frappe role
#: definition and this module should see the same words. ``read`` and ``write``
#: are the two the acceptance criteria name ("role capabilities gate document
#: reads/writes"); the rest are the permission types the upstream model
#: distinguishes and are carried so a later lane does not have to widen a
#: vocabulary it cannot see the edges of.
ACTION_CREATE = "create"
ACTION_WRITE = "write"
ACTION_READ = "read"
ACTION_SUBMIT = "submit"
ACTION_CANCEL = "cancel"
ACTION_AMEND = "amend"
ACTION_DELETE = "delete"

#: The closed ERP action vocabulary.
ACTIONS: Tuple[str, ...] = (
    ACTION_AMEND,
    ACTION_CANCEL,
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_READ,
    ACTION_SUBMIT,
    ACTION_WRITE,
)

#: The actions that may ever be granted to a tenant's own data. Every action in
#: :data:`ACTIONS` is tenant-local; there is deliberately no ``share`` or
#: ``export`` action, because an action that moves data *between* tenants could
#: not be granted without contradicting the tenant gate in ``scope.py``. Widening
#: this set is a design change, not a data change.
TENANT_LOCAL_ACTIONS: Tuple[str, ...] = ACTIONS

# --- the closed refusal vocabulary ------------------------------------------

#: Every reason this package refuses for. Sorted, so a diff reads.
#:
#: Every code here is *raisable*: ``negative_control.py`` provokes each one and
#: the suite fails when the provoked set and this set diverge, so a code that
#: nothing can raise would show up immediately as an unprovoked entry. That is
#: why there is no ``field-read-denied``: a withheld read is communicated by
#: ``Decision.redacted``, not by a refusal, and a code for it would have been
#: an entry no code path could reach.
REFUSALS = frozenset(
    {
        # declaration / schema layer
        "declaration-invalid",
        "schema-violation",
        "harvest-code-copied",
        "harvest-incomplete",
        # the consumed contracts
        "contract-unavailable",
        "unknown-effect",
        # role-map lookup
        "unknown-role",
        "unknown-kind",
        "unknown-action",
        "empty-role-map",
        # the tenant gate (never overridable)
        "cross-tenant",
        "tenant-missing",
        # the platform's two gates, consumed from identity/rbac
        "scope-denied",
        "permission-denied",
        # field-level policy
        "field-write-denied",
        "unknown-field-policy",
        "unknown-field",
        # principal
        "malformed-principal",
    }
)


class Refused(Exception):
    """A refusal, carrying a code from the closed :data:`REFUSALS` vocabulary.

    An unknown ``code`` is refused at the raise rather than propagated: a
    refusal a caller cannot branch on is a bug in the refuser, and catching it
    here is what keeps the vocabulary closed in practice rather than in prose.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in REFUSALS:
            raise ValueError(
                f"Refused({code!r}) — not in the closed refusal vocabulary; "
                f"add it to model.REFUSALS and provoke it in negative_control.py"
            )
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


# --- value types ------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """Who is asking. **Reference-based, never credential-bearing (GR-6).**

    ``tenant`` is the authority for identity: a :class:`Request` that names a
    different tenant is refused, and nothing in a declaration set can change
    that. ``subject`` is the platform subject id (an agent or user) that
    ``identity/rbac`` resolves a scope for. ``roles`` are ERP role *names*,
    looked up in the role map — they are names, not grants: a name that the
    role map does not declare is refused (``unknown-role``) rather than
    silently ignored.
    """

    tenant: str
    subject: str
    roles: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tenant or not self.subject:
            raise Refused("malformed-principal", f"{self.tenant!r}/{self.subject!r}")
        # A principal with no roles is legal and can do nothing: the refusal
        # that follows is the *action* being denied, not the principal being
        # invalid, so it is not refused here.


@dataclass(frozen=True)
class Request:
    """One attempt to act on one ERP document.

    ``tenant`` is the *claimed* tenant. It is separate from
    :attr:`Principal.tenant` on purpose (see the module docstring); the scope
    middleware compares them and refuses a mismatch.

    ``team`` names the team the document belongs to, and exists only to build
    the scope node the platform's scope gate resolves — it is never used to
    pick a tenant. The node's org always comes from the principal.
    """

    tenant: str
    kind: str
    action: str
    document_id: Optional[str] = None
    team: Optional[str] = None
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """The outcome of one authorization attempt.

    ``allowed`` is the only field a caller must branch on. When it is False,
    ``reason`` is a code from :data:`REFUSALS` — so a caller can distinguish
    *why* (a tenant mismatch is not a missing role), which is what makes the
    refusals auditable rather than merely negative.

    When it is True, ``projection`` is the field set the caller may see: a
    field whose policy denies ``read`` is **absent** from it, not blanked, and
    is named in ``redacted``. Removing a field and blanking it are different
    claims — a blanked field still discloses that it exists and carries a
    value — so the projection is built by omission.

    ``advisories`` carries the ids of the non-enforcing field rules that fired.
    A consulted action always resolves to allow, allow-with-a-warning, or deny
    (AO-GR-19 guard honesty): an advisory that fired but was not reported would
    be a rule nobody can act on, which is the "passed silently" state that
    honesty rule exists to forbid.
    """

    allowed: bool
    reason: str = ""
    detail: str = ""
    projection: Mapping[str, Any] = field(default_factory=dict)
    redacted: Tuple[str, ...] = ()
    advisories: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.allowed and self.reason not in REFUSALS:
            raise ValueError(
                f"Decision(allowed=False, reason={self.reason!r}) — reason must be "
                f"a code from the closed refusal vocabulary"
            )


def decision_from_refusal(refusal: Refused) -> Decision:
    """Turn a :class:`Refused` into a denied :class:`Decision`.

    The single conversion point, so ``allowed`` and ``reason`` cannot drift
    apart: every denial the middleware reports goes through here.
    """
    return Decision(allowed=False, reason=refusal.code, detail=refusal.detail)


def is_action(value: Any) -> bool:
    """True when ``value`` is a declared ERP action."""
    return isinstance(value, str) and value in ACTIONS


def is_tenant_local(value: Any) -> bool:
    """True when ``value`` is an action that may be granted inside one tenant."""
    return isinstance(value, str) and value in TENANT_LOCAL_ACTIONS
