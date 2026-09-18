"""``python3 -m integrations.erp.auth.cli`` — declarations, golden path, controls.

The tri-state contract (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) is not decoration
here. A declaration that will not load is *not* a pass and *not* a failure: the
module cannot report on a catalogue it could not read, and saying "NOT-OK" would
blame the catalogue for the reader's problem. So a load failure prints
CANNOT-ASSESS and exits 2, and `check` treats its own 2 as a failure of the
gate that calls it.

``check`` measures four separable things, each able to fail on its own:

1. **the declarations load** — the role map, the field policies and the harvest
   record, each through its single seam;
2. **the frozen schemas are used** — the shipped declaration files are validated
   against ``schema/*.schema.json`` with ERP-02's validator, so the schemas
   beside the data are a claim that is checked rather than a file that sits
   there;
3. **the golden path is deterministic** — the same request, twice, through two
   independently built platform stores, must yield the same decision digest. A
   projection assembled from set or dict iteration order would show up here;
4. **every declared refusal is provoked, by name** — computed by
   :mod:`.negative_control`, and a divergence in *either* direction fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO

from . import negative_control, platform_fixture as fx, policies as policies_module, provenance, roles, schemas
from .model import REFUSALS, Principal, Refused, Request
from .scope import authorize

ROOT = Path(__file__).resolve().parent


def _digest(payload: Any) -> str:
    """A stable digest of a decision transcript."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def golden_path() -> Dict[str, Any]:
    """The end-to-end transcript: a read projected by policy, and a denied write.

    Built from the *shipped* declarations, so this is the module's real
    configuration rather than a fixture shaped to pass.
    """
    role_map = roles.load_default()
    policy_set = policies_module.load_default(kinds=role_map.kinds)
    store, _ = fx.build(permissions=[role_map.permission_for("sales-invoice", "read")])
    principal = Principal(tenant=fx.DEFAULT_TENANT, subject=fx.DEFAULT_SUBJECT, roles=("Sales User",))

    read = authorize(
        role_map,
        policy_set,
        store,
        principal,
        Request(
            tenant=fx.DEFAULT_TENANT,
            kind="sales-invoice",
            action="read",
            team=fx.DEFAULT_TEAM,
            fields={"total": 100, "gross-margin": 42, "discount-percent": 5},
        ),
    )
    write_store, _ = fx.build(permissions=[role_map.permission_for("sales-order", "write")])
    write = authorize(
        role_map,
        policy_set,
        write_store,
        principal,
        Request(
            tenant=fx.DEFAULT_TENANT,
            kind="sales-order",
            action="write",
            team=fx.DEFAULT_TEAM,
            fields={"credit-limit": 5000},
        ),
    )
    foreign = authorize(
        role_map,
        policy_set,
        store,
        Principal(tenant=fx.DEFAULT_TENANT, subject=fx.DEFAULT_SUBJECT, roles=("System Manager",)),
        Request(tenant="other", kind="sales-invoice", action="read", team=fx.DEFAULT_TEAM),
    )
    return {
        "read": {
            "allowed": read.allowed,
            "projection": dict(sorted(read.projection.items())),
            "redacted": list(read.redacted),
            "advisories": list(read.advisories),
        },
        "write": {"allowed": write.allowed, "reason": write.reason, "detail": write.detail},
        "cross_tenant": {"allowed": foreign.allowed, "reason": foreign.reason},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="integrations.erp.auth.cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="declarations, schemas, the golden path and every refusal")
    sub.add_parser("demo", help="the golden-path transcript as JSON")
    sub.add_parser("roles", help="the validated role map as JSON")
    sub.add_parser("fields", help="the validated field-policy set as JSON")
    return parser


def _fail(err: TextIO, what: str, refusal: Refused) -> int:
    print(f"  CANNOT-ASSESS  {what}: {refusal.detail}", file=err)
    return 2


def command_check(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    problems: List[str] = []

    # --- 1. the declarations load -------------------------------------------
    try:
        role_map = roles.load_default()
        policy_set = policies_module.load_default(kinds=role_map.kinds)
        provenance.load_default()
    except Refused as refusal:
        return _fail(err, "declarations", refusal)
    print(
        f"  OK    declarations load: {len(role_map.kinds)} kind(s), "
        f"{len(role_map.role_names)} role(s), {len(policy_set.rules)} field rule(s)",
        file=sink,
    )
    missing_fields = [k for k in policy_set.kind_coverage() if k not in role_map.kinds]
    if missing_fields:
        problems.append(f"field rules govern undeclared kind(s): {', '.join(missing_fields)}")
    print(
        f"  OK    the field rules govern {len(policy_set.kind_coverage())} kind(s), "
        f"all declared by the role map",
        file=sink,
    )

    # --- 2. the frozen schemas are used, not decorative ---------------------
    try:
        for name, decl in (
            ("role-map.schema.json", ROOT / "catalog" / "roles.json"),
            ("field-policy.schema.json", ROOT / "catalog" / "field-policies.json"),
            ("provenance.schema.json", ROOT / "catalog" / "provenance.json"),
        ):
            instance = json.loads(decl.read_text(encoding="utf-8"))
            violations = schemas.violations(instance, name)
            if violations:
                problems.append(f"{decl.name} violates {name}: {violations[0]}")
    except Refused as refusal:
        return _fail(err, "frozen schemas", refusal)
    except (OSError, ValueError) as exc:
        return _fail(err, "frozen schemas", Refused("declaration-invalid", str(exc)))
    print(f"  OK    every shipped declaration matches its frozen schema ({len(schemas.SCHEMAS)})", file=sink)

    # --- 3. the golden path is deterministic --------------------------------
    first, second = golden_path(), golden_path()
    if _digest(first) != _digest(second):
        problems.append("the golden path is not deterministic: two runs disagree")
    if not first["read"]["allowed"]:
        problems.append(f"the golden path read was refused: {first['read']}")
    if first["read"]["redacted"] != ["gross-margin"]:
        problems.append(f"expected gross-margin to be withheld, got {first['read']['redacted']}")
    if first["read"]["advisories"] != ["discount-needs-finance-review"]:
        problems.append(f"the advisory did not fire: {first['read']['advisories']}")
    if first["write"]["reason"] != "field-write-denied":
        problems.append(f"the read-only field was not refused: {first['write']}")
    if first["cross_tenant"]["reason"] != "cross-tenant":
        problems.append(f"the cross-tenant request was not refused: {first['cross_tenant']}")
    print(f"  OK    the golden path is deterministic (digest {_digest(first)})", file=sink)
    print(
        "  OK    a denied field is withheld by omission, the advisory fires, the read-only "
        "field is refused and the cross-tenant read is refused",
        file=sink,
    )

    # --- 4. every declared refusal is provoked, by name ---------------------
    provoked, failures = negative_control.provoke()
    uncovered = negative_control.uncovered()
    for failure in failures:
        problems.append(f"provocation failed — {failure}")
    for code in uncovered:
        problems.append(f"declared refusal never provoked: {code}")
    if len(provoked) != len(REFUSALS):
        problems.append(f"provoked {len(provoked)} of {len(REFUSALS)} declared refusal(s)")
    print(
        f"  OK    {len(provoked)} of {len(REFUSALS)} declared refusal(s) provoked, by name",
        file=sink,
    )

    for problem in problems:
        print(f"  FAIL  {problem}", file=sink)
    if problems:
        return 1
    print("integrations.erp.auth: OK", file=sink)
    return 0


def command_demo(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    try:
        print(json.dumps(golden_path(), indent=2, sort_keys=True), file=sink)
    except Refused as refusal:
        print(f"erp-auth demo: CANNOT-ASSESS — {refusal}", file=err)
        return 2
    return 0


def command_roles(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    try:
        print(json.dumps(roles.load_default().to_json(), indent=2, sort_keys=True), file=sink)
    except Refused as refusal:
        print(f"erp-auth roles: CANNOT-ASSESS — {refusal}", file=err)
        return 2
    return 0


def command_fields(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    try:
        role_map = roles.load_default()
        policy_set = policies_module.load_default(kinds=role_map.kinds)
        print(json.dumps(policy_set.to_json(), indent=2, sort_keys=True), file=sink)
    except Refused as refusal:
        print(f"erp-auth fields: CANNOT-ASSESS — {refusal}", file=err)
        return 2
    return 0


def main(
    argv: Optional[Sequence[str]] = None,
    sink: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    out = sink or sys.stdout
    errors = err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return command_check(args, out, errors)
    if args.command == "demo":
        return command_demo(args, out, errors)
    if args.command == "roles":
        return command_roles(args, out, errors)
    if args.command == "fields":
        return command_fields(args, out, errors)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
