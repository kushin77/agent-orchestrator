"""Field-level policy declarations — schema-validated, and actually enforced.

Acceptance criterion 2 of ERP-08 is that a field-level declaration is *enforced*,
not merely declared, and that a negative control proves a deny really denies.
That gives this module two jobs and one rule about how they relate.

**The enforcement is by omission.** :meth:`FieldPolicySet.project` builds the
visible field set by *leaving fields out*; it never blanks them. The difference
matters: a blanked field still discloses that the field exists and carries a
value, and an auditor cannot tell "absent" from "empty". The names of what was
withheld are returned separately (``redacted``), so the withholding itself is
auditable rather than invisible.

**Both directions of the guardrails vocabulary are load-bearing (AO-GR-19).**
``effect`` must be a word from ``guardrails/policy``'s ``DecisionLevel`` —
consumed, never re-typed (see :mod:`contract`). It is not decoration in either
direction, and a declaration that uses it as decoration is refused by name:

* an **enforcing** rule (``read: false`` or ``write: false``) must declare
  ``block``. A denial declared ``log`` would tell guardrails to allow and
  observe an action this module is about to refuse — the two layers would
  disagree about the same request;
* an **advisory** rule (``read: true`` *and* ``write: true``) must declare
  ``warn`` or ``log``. An advisory rule declared ``block`` blocks nothing —
  it is a rule that cannot fail, which GR-12 calls a formality.

So a rule is either an enforcement that enforces, or advice that advises; there
is no third shape, and both mistakes are refused by name. An advisory that fires
is reported on the :class:`~integrations.erp.auth.model.Decision` — a consulted
action resolves to allow-with-a-warning rather than passing silently.

---knowledge---
module_id: integrations.erp.auth.policies
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [FieldRule, FieldPolicySet, load, load_default]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

from . import contract
from .model import SCHEMA_VERSION, Refused

#: The effect word that means "this rule enforces": the value the consumed
#: contract gives to a *denying* decision.
BLOCK_EFFECT = "block"


@dataclass(frozen=True)
class FieldRule:
    """One field-level declaration over ``kind.field``."""

    id: str
    kind: str
    field: str
    effect: str
    read: bool
    write: bool
    roles: Tuple[str, ...] = ()
    reason: str = ""

    @property
    def enforces(self) -> bool:
        """True when this rule denies something (read or write is withheld)."""
        return not (self.read and self.write)

    def applies_to(self, roles: Iterable[str]) -> bool:
        """True when this rule governs a principal holding any of ``roles``.

        An empty ``roles`` means every principal: a rule not scoped to roles is
        a rule about the *field*, not about who may see it.
        """
        if not self.roles:
            return True
        return any(role in self.roles for role in roles)


@dataclass(frozen=True)
class FieldPolicySet:
    """A validated set of field-level rules."""

    version: int
    rules: Tuple[FieldRule, ...]

    def covering(
        self, kind: str, field: str, roles: Iterable[str]
    ) -> Tuple[FieldRule, ...]:
        """Every rule governing ``kind.field`` for a principal holding ``roles``."""
        return tuple(
            r for r in self.rules if r.kind == kind and r.field == field and r.applies_to(roles)
        )

    def read_denied(self, kind: str, field: str, roles: Iterable[str]) -> FieldRule | None:
        """The enforcing rule that withholds ``kind.field`` from ``roles``, if any."""
        for rule in self.covering(kind, field, roles):
            if rule.enforces and not rule.read:
                return rule
        return None

    def write_denied(self, kind: str, field: str, roles: Iterable[str]) -> FieldRule | None:
        """The enforcing rule that makes ``kind.field`` read-only for ``roles``, if any."""
        for rule in self.covering(kind, field, roles):
            if rule.enforces and not rule.write:
                return rule
        return None

    def advisories(self, kind: str, field: str, roles: Iterable[str]) -> Tuple[str, ...]:
        """Ids of the non-enforcing rules that fire for ``kind.field`` and ``roles``.

        Returned so a decision can report them: an advisory that is never
        surfaced is a rule nobody can act on, and "passed silently" is the
        state AO-GR-19 exists to forbid.
        """
        return tuple(
            r.id for r in self.covering(kind, field, roles) if not r.enforces
        )

    def kind_coverage(self) -> Tuple[str, ...]:
        """The kinds at least one rule governs, sorted."""
        return tuple(sorted({r.kind for r in self.rules}))

    def fields_for(self, kind: str) -> Tuple[str, ...]:
        """The fields declared for ``kind``, sorted."""
        return tuple(sorted({r.field for r in self.rules if r.kind == kind}))

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "rules": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "field": r.field,
                    "effect": r.effect,
                    "read": r.read,
                    "write": r.write,
                    "roles": list(r.roles),
                    "reason": r.reason,
                }
                for r in self.rules
            ],
        }


def _as_mapping(source: Any, what: str) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise Refused("declaration-invalid", f"{what}: no such file {path}")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refused("declaration-invalid", f"{what}: unreadable ({exc})") from exc
        if not isinstance(loaded, Mapping):
            raise Refused("declaration-invalid", f"{what}: top level is not an object")
        return loaded
    raise Refused("declaration-invalid", f"{what}: expected a mapping or a path")


def load(source: Any, *, kinds: Iterable[str] | None = None) -> FieldPolicySet:
    """Load and fully validate a field-policy set from a path or a mapping.

    ``kinds``, when given, is the set of document kinds the role map covers; a
    rule about a kind outside it is refused (``unknown-kind``) rather than
    sitting in the file governing nothing. Passing it is how ``cli.check``
    cross-checks the two declarations against each other.
    """
    document = _as_mapping(source, "field policies")

    version = document.get("version")
    if version != SCHEMA_VERSION:
        raise Refused("declaration-invalid", f"version must be {SCHEMA_VERSION}, got {version!r}")

    rules_doc = document.get("rules")
    if not isinstance(rules_doc, list) or not rules_doc:
        raise Refused("declaration-invalid", "no field rules are declared")

    vocabulary = contract.effect_vocabulary()
    known_kinds = tuple(kinds) if kinds is not None else None
    seen_ids: set[str] = set()
    rules = []
    for entry in rules_doc:
        if not isinstance(entry, Mapping):
            raise Refused("declaration-invalid", f"{entry!r} is not a rule object")
        rule_id = entry.get("id")
        kind = entry.get("kind")
        field = entry.get("field")
        effect = entry.get("effect")
        read = entry.get("read")
        write = entry.get("write")
        roles = entry.get("roles", [])
        reason = entry.get("reason", "")

        if not isinstance(rule_id, str) or not rule_id:
            raise Refused("declaration-invalid", f"rule id {rule_id!r} is not a name")
        if rule_id in seen_ids:
            raise Refused("unknown-field-policy", f"duplicate rule id {rule_id!r}")
        seen_ids.add(rule_id)

        if not isinstance(kind, str) or not kind:
            raise Refused("declaration-invalid", f"rule {rule_id!r}: kind {kind!r} is not a name")
        if known_kinds is not None and kind not in known_kinds:
            raise Refused(
                "unknown-kind",
                f"rule {rule_id!r} governs {kind!r}, which the role map does not cover "
                f"(covered: {', '.join(known_kinds)})",
            )
        if not isinstance(field, str) or not field or "*" in field:
            raise Refused(
                "unknown-field",
                f"rule {rule_id!r}: field {field!r} is not a single field name",
            )
        if not isinstance(effect, str) or effect not in vocabulary:
            raise Refused(
                "unknown-effect",
                f"rule {rule_id!r}: {effect!r} is not an effect of the guardrails "
                f"vocabulary ({', '.join(vocabulary)})",
            )
        if not isinstance(read, bool) or not isinstance(write, bool):
            raise Refused(
                "declaration-invalid",
                f"rule {rule_id!r}: read/write must be booleans, got {read!r}/{write!r}",
            )
        if not isinstance(roles, list) or any(not isinstance(r, str) or not r for r in roles):
            raise Refused(
                "declaration-invalid", f"rule {rule_id!r}: roles must be a list of role names"
            )
        if len(set(roles)) != len(roles):
            raise Refused("declaration-invalid", f"rule {rule_id!r}: roles repeat a name")
        if not isinstance(reason, str) or not reason.strip():
            raise Refused(
                "declaration-invalid",
                f"rule {rule_id!r}: a rule must carry a reason — an unexplained denial "
                f"cannot be reviewed",
            )

        enforces = not (read and write)
        if enforces and effect != BLOCK_EFFECT:
            raise Refused(
                "declaration-invalid",
                f"rule {rule_id!r} withholds a field but declares effect {effect!r}: guardrails "
                f"would allow and observe what this rule refuses — the two layers would "
                f"disagree about the same request",
            )
        if not enforces and effect == BLOCK_EFFECT:
            raise Refused(
                "declaration-invalid",
                f"rule {rule_id!r} declares effect {BLOCK_EFFECT!r} while withholding neither "
                f"read nor write — a rule that denies nothing is a formality",
            )

        rules.append(
            FieldRule(
                id=rule_id,
                kind=kind,
                field=field,
                effect=effect,
                read=read,
                write=write,
                roles=tuple(roles),
                reason=reason,
            )
        )

    return FieldPolicySet(version=version, rules=tuple(rules))


def load_default(*, kinds: Iterable[str] | None = None) -> FieldPolicySet:
    """The shipped field policies, from ``catalog/field-policies.json``."""
    return load(Path(__file__).resolve().parent / "catalog" / "field-policies.json", kinds=kinds)
