#!/usr/bin/env python3
"""Authority CLI — the gate-facing surface of the authority model (issue #150).

Exit-code contract (guardrails/honesty tri-state, issue #28):

* ``0`` OK            — the decision is ALLOW / the matrix is valid / every control met
* ``1`` NOT-OK        — an explicit DENIAL / a real document defect / a failed control
* ``2`` CANNOT-ASSESS — the matrix or schema cannot be read, or a reference does
  not exist, so no verdict can be reached. CANNOT-ASSESS is never a pass.

Commands:

    validate                       [--matrix M] [--schema S]
    can-act  --principal P --repo R --action A
    sod      --work-item W
    closure  [--work-item W]
    isolation
    controls [--controls C]
    selfcheck [--controls C]
    matrix

``validate`` is the document check (schema + semantic invariants);
``can-act``, ``sod`` and ``closure`` are the decisions; ``selfcheck`` runs the
declared behavioral controls in ``controls.yaml`` against the live engine, which
is what gives the authority gate its teeth — a weakened scoping, separation or
closure rule fails a control instead of passing silently.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PKG_DIR = Path(__file__).resolve().parent
if str(PKG_DIR) not in sys.path:  # standalone module, no package __init__.py
    sys.path.insert(0, str(PKG_DIR))

import isolation as isolation_mod  # noqa: E402  (needs the sys.path bootstrap)
import model  # noqa: E402

DEFAULT_CONTROLS_PATH = PKG_DIR / "controls.yaml"

_EXPECT_TO_VERDICT: Mapping[str, str] = {
    "OK": model.Verdict.ALLOW.value,
    "NOT-OK": model.Verdict.DENY.value,
    "ALLOW": model.Verdict.ALLOW.value,
    "DENY": model.Verdict.DENY.value,
    "CANNOT-ASSESS": model.Verdict.CANNOT_ASSESS.value,
}


def _emit(payload: Mapping[str, Any], as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(human)


def _load(matrix_path: Path | str, schema_path: Path | str) -> Tuple[Optional[model.Matrix], int]:
    """Load the matrix; on an unreadable/unsupported input return rc 2 (never a pass)."""
    try:
        return model.load_matrix(matrix_path, schema_path), 0
    except model.MatrixLoadError as exc:
        print(f"authority: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return None, 2
    except model.SchemaError as exc:
        print(f"authority: CANNOT-ASSESS — schema unusable: {exc}", file=sys.stderr)
        return None, 2


def cmd_validate(args: argparse.Namespace) -> int:
    matrix, rc = _load(args.matrix, args.schema)
    if matrix is None:
        print("valid: cannot-assess")
        return rc
    verdict, findings = matrix.verdict()
    if verdict is model.Verdict.CANNOT_ASSESS:
        for one in findings:
            print(f"  cannot-assess  {one}", file=sys.stderr)
        _emit({"valid": None, "verdict": verdict.value, "findings": findings}, args.json, "valid: cannot-assess")
        return verdict.exit_code
    if verdict is model.Verdict.DENY:
        for one in findings:
            print(f"  NOT-OK  {one}", file=sys.stderr)
        _emit(
            {"valid": False, "verdict": verdict.value, "findings": findings},
            args.json,
            f"valid: NO ({len(findings)} finding(s))",
        )
        return verdict.exit_code
    _emit(
        {
            "valid": True,
            "verdict": verdict.value,
            "repos": [one.id for one in matrix.repos],
            "principals": [one.id for one in matrix.principals],
            "work_items": [one.id for one in matrix.work_items],
        },
        args.json,
        f"valid: yes ({len(matrix.repos)} repo(s), {len(matrix.principals)} principal(s), "
        f"{len(matrix.work_items)} work item(s))",
    )
    return 0


def cmd_can_act(args: argparse.Namespace) -> int:
    matrix, rc = _load(args.matrix, args.schema)
    if matrix is None:
        return rc
    decision = model.can_act(matrix, args.principal, args.repo, args.action)
    _emit(decision.to_dict(), args.json, f"can-act: {decision.render()}")
    return decision.verdict.exit_code


def cmd_sod(args: argparse.Namespace) -> int:
    matrix, rc = _load(args.matrix, args.schema)
    if matrix is None:
        return rc
    decision = model.separation_of_duties(matrix, args.work_item)
    _emit(decision.to_dict(), args.json, f"sod: {decision.render()}")
    return decision.verdict.exit_code


def cmd_closure(args: argparse.Namespace) -> int:
    matrix, rc = _load(args.matrix, args.schema)
    if matrix is None:
        return rc
    if args.work_item:
        decisions = [model.is_closed(matrix, args.work_item)]
    else:
        decisions = model.closure_report(matrix)
    if not decisions:
        print("closure: CANNOT-ASSESS — the matrix declares no work item", file=sys.stderr)
        return 2
    worst = max((one.verdict.exit_code for one in decisions), default=2)
    for decision in decisions:
        print(f"closed: {decision.render()}")
    if args.json:
        print(json.dumps({"decisions": [one.to_dict() for one in decisions], "exit_code": worst}, indent=2, sort_keys=True))
    return worst


def cmd_isolation(args: argparse.Namespace) -> int:
    matrix, rc = _load(args.matrix, args.schema)
    if matrix is None:
        return rc
    if not matrix.valid:
        verdict, findings = matrix.verdict()
        for one in findings:
            print(f"  {verdict.value}  {one}", file=sys.stderr)
        print("isolation: CANNOT-ASSESS — the matrix itself is not usable", file=sys.stderr)
        return verdict.exit_code
    report = isolation_mod.verify_isolation(matrix)
    for step in report.steps:
        if not args.json:
            print(step.render())
    for finding in report.findings:
        print(f"  FINDING  {finding}", file=sys.stderr)
    human = (
        f"isolation: {'OK' if report.isolated else 'NOT-OK'} — {len(report.repos)} repo(s) isolated, "
        f"cross-repo actor(s): {list(report.cross_repo_principals)}"
    )
    _emit(report.to_dict(), args.json, human)
    return 0 if report.isolated else 1


def _read_controls(path: Path | str) -> Mapping[str, Any]:
    document = model.read_matrix_document(path)
    controls = document.get("controls")
    if not isinstance(controls, list) or not controls:
        raise model.MatrixLoadError(f"{path} declares no controls — an empty control set proves nothing")
    return document


def cmd_controls(args: argparse.Namespace) -> int:
    try:
        document = _read_controls(args.controls)
    except (model.MatrixLoadError, model.SchemaError) as exc:
        print(f"controls: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2
    for control in document["controls"]:
        print(f"  {control.get('kind', '?'):<10} {control.get('id', '?')} -> expect {control.get('expect', '?')}")
    print(f"controls: {len(document['controls'])} declared")
    return 0


def _evaluate_control(
    control: Mapping[str, Any],
    document: Mapping[str, Any],
    matrix_path: Path | str,
    schema_path: Path | str,
) -> Tuple[str, str]:
    """Return (verdict, reason) for one declared control, evaluated against the live engine."""
    kind = str(control.get("kind", ""))
    overlay = control.get("overlay")

    if kind == "load":
        if control.get("missing"):
            with tempfile.TemporaryDirectory(prefix="ao150-control-") as tmp:
                missing = Path(tmp) / "matrix.yaml"
                try:
                    model.load_matrix(missing, schema_path)
                except (model.MatrixLoadError, model.SchemaError) as exc:
                    return model.Verdict.CANNOT_ASSESS.value, str(exc)
            return model.Verdict.ALLOW.value, "a missing matrix unexpectedly loaded"

        with tempfile.TemporaryDirectory(prefix="ao150-control-") as tmp:
            raw_path = Path(tmp) / "matrix.yaml"
            raw_path.write_text(str(control.get("raw", "")), encoding="utf-8")
            try:
                matrix = model.load_matrix(raw_path, schema_path)
            except (model.MatrixLoadError, model.SchemaError) as exc:
                return model.Verdict.CANNOT_ASSESS.value, str(exc)
        return _document_verdict(matrix)

    if kind == "validate":
        matrix = model.matrix_from_overlay(document, overlay, schema_path)
        return _document_verdict(matrix)

    matrix = model.matrix_from_overlay(document, overlay, schema_path)

    if kind == "can-act":
        decision = model.can_act(matrix, str(control.get("principal")), str(control.get("repo")), str(control.get("action")))
    elif kind == "sod":
        decision = model.separation_of_duties(matrix, str(control.get("work_item")))
    elif kind == "closure":
        decision = model.is_closed(matrix, str(control.get("work_item")))
    elif kind == "isolation":
        if not matrix.valid:
            return _document_verdict(matrix)
        report = isolation_mod.verify_isolation(matrix)
        return (
            (model.Verdict.ALLOW.value if report.isolated else model.Verdict.DENY.value),
            "; ".join(report.findings) or "two repos isolated",
        )
    else:
        return model.Verdict.CANNOT_ASSESS.value, f"control kind {kind!r} is not implemented"
    return decision.verdict.value, decision.reason


def _document_verdict(matrix: model.Matrix) -> Tuple[str, str]:
    verdict, findings = matrix.verdict()
    if verdict is model.Verdict.ALLOW:
        return model.Verdict.ALLOW.value, "document valid"
    return verdict.value, "; ".join(findings) or "invalid"


def cmd_selfcheck(args: argparse.Namespace) -> int:
    matrix, rc = _load(args.matrix, args.schema)
    if matrix is None:
        return rc
    if matrix.faults:
        for one in matrix.faults:
            print(f"  {model.Verdict.CANNOT_ASSESS.value}  {one}", file=sys.stderr)
        print("selfcheck: CANNOT-ASSESS — the shipped matrix cannot be assessed", file=sys.stderr)
        return 2
    try:
        document = _read_controls(args.controls)
    except (model.MatrixLoadError, model.SchemaError) as exc:
        print(f"selfcheck: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2

    failures: List[Dict[str, Any]] = []
    declared = 0
    for control in document["controls"]:
        declared += 1
        control_id = str(control.get("id", "<unnamed>"))
        expect = str(control.get("expect", ""))
        if expect not in _EXPECT_TO_VERDICT:
            print(f"  MISMATCH  {control_id}: expect {expect!r} is not a tri-state verdict", file=sys.stderr)
            failures.append({"id": control_id, "expect": expect, "actual": "unusable-expectation", "reason": ""})
            continue
        actual, reason = _evaluate_control(control, matrix.document, args.matrix, args.schema)
        met = actual == _EXPECT_TO_VERDICT[expect]
        marker = "met     " if met else "MISMATCH"
        print(f"  {marker}  {control_id:<62} expect {expect:<13} got {actual}")
        if not met:
            failures.append({"id": control_id, "expect": expect, "actual": actual, "reason": reason})
            print(f"            {reason}", file=sys.stderr)

    if failures:
        print(f"selfcheck: FAIL — {len(failures)} of {declared} control(s) did not meet their declared verdict", file=sys.stderr)
        if args.json:
            print(json.dumps({"declared": declared, "failed": failures}, indent=2, sort_keys=True))
        return 1
    print(f"selfcheck: OK — all {declared} control(s) met their declared verdict")
    if args.json:
        print(json.dumps({"declared": declared, "failed": []}, indent=2, sort_keys=True))
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    matrix, rc = _load(args.matrix, args.schema)
    if matrix is None:
        return rc
    if args.json:
        payload = {
            "valid": matrix.valid,
            "violations": list(matrix.violations),
            "faults": list(matrix.faults),
            "repos": [one.to_dict() for one in matrix.repos],
            "principals": [one.to_dict() for one in matrix.principals],
            "work_items": [one.to_dict() for one in matrix.work_items],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return matrix.verdict()[0].exit_code
    for repo in matrix.repos:
        print(f"repo {repo.id}  fleet={repo.fleet}  rights={list(repo.admin_rights)}  gate={repo.gate!r}")
    for principal in matrix.principals:
        marker = " (cross-repo)" if principal.cross_repo else ""
        print(f"principal {principal.id}  kind={principal.kind}  scope={list(principal.scope)}{marker}")
        for actor in principal.actors:
            merge = " merge-authority" if actor.merge_authority else ""
            print(f"  actor {actor.id:<24} role={actor.role:<20} posture={actor.posture:<9} tier={actor.model_tier}{merge}")
    for item in matrix.work_items:
        sod = item.sod
        print(
            f"work-item {item.id}  repo={item.repo}  executor={sod.get('executor')} "
            f"reviewer={sod.get('reviewer')} auditor={sod.get('auditor')}"
        )
    verdict, findings = matrix.verdict()
    for one in findings:
        print(f"  {verdict.value}  {one}")
    print(f"matrix: {verdict.value}")
    return verdict.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/authority/cli.py",
        description="Authority matrix enforcement: scoped admin rights, separation of duties, e2e closure.",
    )
    parser.add_argument("--matrix", default=str(model.DEFAULT_MATRIX_PATH), help="path to matrix.yaml")
    parser.add_argument("--schema", default=str(model.DEFAULT_SCHEMA_PATH), help="path to schema.json")
    parser.add_argument("--controls", default=str(DEFAULT_CONTROLS_PATH), help="path to controls.yaml")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate the matrix (schema + semantic invariants)")
    sub.add_parser("matrix", help="print the normalized matrix and its assessment")
    sub.add_parser("isolation", help="demonstrate two-repo isolation")
    sub.add_parser("controls", help="list the declared behavioral controls")
    sub.add_parser("selfcheck", help="run every declared control against the live engine")

    p_can = sub.add_parser("can-act", help="decide whether a principal may act on a repo")
    p_can.add_argument("--principal", required=True)
    p_can.add_argument("--repo", required=True)
    p_can.add_argument("--action", required=True, help=f"one of {list(model.ACTIONS)}")

    p_sod = sub.add_parser("sod", help="check separation of duties for a work item")
    p_sod.add_argument("--work-item", required=True)

    p_close = sub.add_parser("closure", help="check end-to-end closure for a work item (or all)")
    p_close.add_argument("--work-item", default=None)
    return parser


_COMMANDS = {
    "validate": cmd_validate,
    "can-act": cmd_can_act,
    "sod": cmd_sod,
    "closure": cmd_closure,
    "isolation": cmd_isolation,
    "controls": cmd_controls,
    "selfcheck": cmd_selfcheck,
    "matrix": cmd_matrix,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
