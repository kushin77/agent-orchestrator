"""One provocation per refusal, and a coverage count that fails when they diverge.

Every code in :data:`~integrations.erp.auth.model.REFUSALS` is provoked here, and
each provocation asserts the refusal **by name**: a provoker that raises a
*different* code is a failure, not a pass. That distinction is the whole point —
"something went wrong" is not evidence that the rule under test is the thing
that refused.

``cli.check`` fails the module's own gate when the provoked set and the declared
vocabulary differ, so a new refusal cannot ship without a control that
demonstrates it refusing, and a refusal that becomes unreachable is reported
rather than silently retained.

Run it directly for the transcript:

    python3 integrations/erp/auth/negative_control.py

---knowledge---
module_id: integrations.erp.auth.negative_control
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [provoke, uncovered, main]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Tuple

from . import contract, platform_fixture as fx, policies as policies_module, provenance, roles, schemas
from .model import REFUSALS, Principal, Refused, Request
from .scope import authorize

# A minimal, valid role map used as the base for the declaration-level provocations.
_GOOD_ROLE_MAP = {
    "version": 1,
    "kinds": ["sales-invoice"],
    "roles": {"Sales User": [{"kind": "sales-invoice", "action": "read"}]},
}

_GOOD_POLICY = {
    "version": 1,
    "rules": [
        {
            "id": "ok",
            "kind": "sales-invoice",
            "field": "gross-margin",
            "effect": "block",
            "read": False,
            "write": False,
            "roles": ["Sales User"],
            "reason": "a finance figure",
        }
    ],
}

_GOOD_PROVENANCE = {
    "schema": "erp.auth.provenance/v1",
    "upstream": "frappe/erpnext",
    "license": "GPL-3.0",
    "mode": "pattern-only",
    "harvests": [
        {
            "shape": "a shape",
            "upstreamPath": "a path",
            "mode": "pattern-only",
            "builtInstead": "something we built",
        }
    ],
}


def _roles_map():
    return roles.load_default()


def _policies_set(kinds):
    return policies_module.load_default(kinds=kinds)


def _read_request(**kwargs) -> Request:
    base = dict(tenant=fx.DEFAULT_TENANT, kind="sales-invoice", action="read", team=fx.DEFAULT_TEAM)
    base.update(kwargs)
    return Request(**base)


# --- the provocations -------------------------------------------------------


def _provoke_declaration_invalid() -> None:
    roles.load({"version": 99, "kinds": ["x"], "roles": {"R": [{"kind": "x", "action": "read"}]}})


def _provoke_schema_violation() -> None:
    schemas.enforce({"version": "not-a-number"}, "role-map.schema.json", "a role map")


def _provoke_harvest_code_copied() -> None:
    bad = dict(_GOOD_PROVENANCE, mode="code-copied")
    provenance.load(bad)


def _provoke_harvest_incomplete() -> None:
    bad = dict(_GOOD_PROVENANCE, harvests=[])
    provenance.load(bad)


def _provoke_contract_unavailable() -> None:
    """Simulate an unreadable guardrails vocabulary.

    No cache eviction is needed: ``contract.decision_levels`` checks the path on
    every call and keys its cache to the path it loaded from, so this provocation
    means the same thing whether or not the vocabulary was read earlier.
    """
    original = contract.DECISION_MODULE_PATH
    contract.DECISION_MODULE_PATH = Path("/nonexistent/decision.py")
    try:
        contract.effect_vocabulary()
    finally:
        contract.DECISION_MODULE_PATH = original


def _provoke_unknown_effect() -> None:
    bad = {"version": 1, "rules": [dict(_GOOD_POLICY["rules"][0], effect="maybe")]}
    policies_module.load(bad)


def _provoke_unknown_role() -> None:
    _roles_map().granted(("No Such Role",), "sales-invoice", "read")


def _provoke_unknown_kind() -> None:
    _roles_map().granted(("Sales User",), "no-such-kind", "read")


def _provoke_unknown_action() -> None:
    _roles_map().granted(("Sales User",), "sales-invoice", "teleport")


def _provoke_empty_role_map() -> None:
    roles.load({"version": 1, "kinds": ["sales-invoice"], "roles": {}})


def _provoke_cross_tenant():
    """The principal holds *every* role, and is still refused."""
    rm = _roles_map()
    store, _ = fx.build(permissions=[rm.permission_for("sales-invoice", "read")])
    principal = Principal(tenant="acme", subject=fx.DEFAULT_SUBJECT, roles=("System Manager",))
    return authorize(rm, _policies_set(rm.kinds), store, principal, _read_request(tenant="other"))


def _provoke_tenant_missing():
    rm = _roles_map()
    store, _ = fx.build(permissions=[rm.permission_for("sales-invoice", "read")])
    principal = Principal(tenant="acme", subject=fx.DEFAULT_SUBJECT, roles=("Sales User",))
    return authorize(rm, _policies_set(rm.kinds), store, principal, _read_request(tenant=""))


def _provoke_scope_denied():
    """The tenant matches and the ERP role grants — but the subject has no binding."""
    rm = _roles_map()
    rbac = contract.rbac()
    store = rbac.InMemoryStore()
    store.add_org(fx.DEFAULT_TENANT, "Acme", tenant_type="platform")
    store.add_team(fx.DEFAULT_TENANT, "ERP", team_id=fx.DEFAULT_TEAM)
    store.add_agent(fx.DEFAULT_TENANT, fx.DEFAULT_TEAM, fx.DEFAULT_SUBJECT, agent_id=fx.DEFAULT_SUBJECT)
    principal = Principal(tenant="acme", subject=fx.DEFAULT_SUBJECT, roles=("Sales User",))
    return authorize(rm, _policies_set(rm.kinds), store, principal, _read_request())


def _provoke_permission_denied():
    """In scope, ERP role grants — and the platform does not, so the platform decides."""
    rm = _roles_map()
    store, _ = fx.build(permissions=[])  # a binding, with no permission on it
    principal = Principal(tenant="acme", subject=fx.DEFAULT_SUBJECT, roles=("Sales User",))
    return authorize(rm, _policies_set(rm.kinds), store, principal, _read_request())


def _provoke_field_write_denied():
    rm = _roles_map()
    store, _ = fx.build(permissions=[rm.permission_for("sales-order", "write")])
    principal = Principal(tenant="acme", subject=fx.DEFAULT_SUBJECT, roles=("Sales User",))
    request = Request(
        tenant="acme",
        kind="sales-order",
        action="write",
        team=fx.DEFAULT_TEAM,
        fields={"credit-limit": 5000},
    )
    return authorize(rm, _policies_set(rm.kinds), store, principal, request)


def _provoke_unknown_field_policy() -> None:
    bad = {"version": 1, "rules": [_GOOD_POLICY["rules"][0], _GOOD_POLICY["rules"][0]]}
    policies_module.load(bad)


def _provoke_unknown_field() -> None:
    bad = {"version": 1, "rules": [dict(_GOOD_POLICY["rules"][0], field="bad*field")]}
    policies_module.load(bad)


def _provoke_malformed_principal() -> None:
    Principal(tenant="", subject="someone")


#: ``(code, description, provoker)`` — one row per declared refusal.
PROVOCATIONS: Tuple[Tuple[str, str, Callable[[], None]], ...] = (
    ("declaration-invalid", "a role map whose version is not the declared one", _provoke_declaration_invalid),
    ("schema-violation", "a declaration that does not match its frozen schema", _provoke_schema_violation),
    ("harvest-code-copied", "a harvest record claiming code was copied", _provoke_harvest_code_copied),
    ("harvest-incomplete", "a harvest record with no harvests in it", _provoke_harvest_incomplete),
    ("contract-unavailable", "the guardrails vocabulary read from a missing file", _provoke_contract_unavailable),
    ("unknown-effect", "a field rule whose effect is not in the contract's vocabulary", _provoke_unknown_effect),
    ("unknown-role", "a principal naming a role the map does not declare", _provoke_unknown_role),
    ("unknown-kind", "a request for a kind the map does not cover", _provoke_unknown_kind),
    ("unknown-action", "a request for an action outside the ERP action vocabulary", _provoke_unknown_action),
    ("empty-role-map", "a declaration that declares no roles", _provoke_empty_role_map),
    ("cross-tenant", "a request claiming another tenant, with a principal holding every role", _provoke_cross_tenant),
    ("tenant-missing", "a request that names no tenant at all", _provoke_tenant_missing),
    ("scope-denied", "a subject with no binding in the tenant the request resolves to", _provoke_scope_denied),
    ("permission-denied", "an in-scope subject whose platform role lacks the translated permission", _provoke_permission_denied),
    ("field-write-denied", "a write carrying a field whose policy is read-only", _provoke_field_write_denied),
    ("unknown-field-policy", "two field rules sharing an id", _provoke_unknown_field_policy),
    ("unknown-field", "a field rule naming something that is not a single field", _provoke_unknown_field),
    ("malformed-principal", "a principal with no tenant", _provoke_malformed_principal),
)


def provoke() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Run every provocation.

    Returns ``(provoked, failures)``. A refusal travels this module by one of two
    channels, and a control must show the rule refusing on the channel it
    actually uses:

    * the **declaration layer** raises :class:`Refused` (a catalogue that will not
      load cannot be assessed);
    * the **authorization layer** returns a denied :class:`Decision` (the request
      is answerable, and the answer is no).

    So a provoker may raise or return; either way the *code* must match, because
    a control that refuses for the wrong reason proves nothing about the rule it
    claims to exercise.
    """
    provoked: list[str] = []
    failures: list[str] = []
    for code, description, provoker in PROVOCATIONS:
        try:
            outcome = provoker()
        except Refused as refusal:
            if refusal.code == code:
                provoked.append(code)
            else:
                failures.append(
                    f"{code}: refused as {refusal.code!r} instead ({description})"
                )
        except Exception as exc:  # noqa: BLE001 — a non-Refused escape is a broken control
            failures.append(f"{code}: raised {type(exc).__name__}: {exc} ({description})")
        else:
            reason = getattr(outcome, "reason", None)
            if reason == code:
                provoked.append(code)
            elif outcome is None:
                failures.append(f"{code}: NOT PROVOKED — nothing refused ({description})")
            else:
                failures.append(
                    f"{code}: decided {reason!r} instead of refusing ({description})"
                )
    return tuple(provoked), tuple(failures)


def uncovered() -> Tuple[str, ...]:
    """Codes declared in :data:`REFUSALS` that no provocation can reach."""
    return tuple(sorted(REFUSALS - {row[0] for row in PROVOCATIONS}))


def main() -> int:
    provoked, failures = provoke()
    print(f"provoked {len(provoked)} of {len(REFUSALS)} declared refusal(s)")
    for code in sorted(provoked):
        print(f"  OK    {code}")
    for failure in failures:
        print(f"  FAIL  {failure}")
    missing = uncovered()
    for code in missing:
        print(f"  FAIL  {code}: declared but never provoked")
    if failures or missing:
        return 1
    print("negative_control: every declared refusal is provoked, by name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
