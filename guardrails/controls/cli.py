"""Command-line interface for the server-side guardrail controls (issue #343).

Subcommands (exit codes are honest and documented):

``list``
    List the registry: id, enabled, mode, owner, live status code.

``get CONTROL_ID``
    Print one control (registry facts + current state) as JSON.

``toggle CONTROL_ID``
    Flip one control.  ``--on`` / ``--off`` set the target explicitly; the
    default flips current state.  Writes EXACTLY ONE append-only audit record
    and persists state so a second reader sees it.  Refuses an unknown control.

``check-report``
    Report the Portkey-style guardrail status code (246 PASSED / 446 BLOCKED)
    for every control.  Exit 0 when every control is default-OFF (all PASSED),
    exit 1 when any control is enabled, exit 2 when the report cannot be built.

``self-test``
    Run this lane's invariants, including the self-mutating negative control
    (a control that ships ON must be refused).  Exit 0 = all invariants bite;
    exit 1 = a check FAILED (printed); exit 2 = cannot assess.

Global ``--controls PATH`` points at the registry (default: the shipped
``guardrails/policy/controls.yaml``); ``--state PATH`` / ``--audit PATH``
select the state and audit files (defaults live beside the registry).

The CLI bootstraps ``guardrails/`` onto ``sys.path`` (mirroring
``policy/cli.py``), so it runs from the repository root or from anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_here = os.path.dirname(os.path.abspath(__file__))
_guardrails_root = os.path.dirname(_here)
if _guardrails_root not in sys.path:
    sys.path.insert(0, _guardrails_root)

from controls.audit import JsonlControlAuditLog  # noqa: E402
from controls.model import (  # noqa: E402
    GUARDRAIL_STATUS,
    STATUS_BLOCKED,
    STATUS_PASSED,
    ControlError,
    UnknownControlError,
    assert_refuses_default_on,
    is_status,
    status_name,
)
from controls.registry import (  # noqa: E402
    build_control_set,
    cross_check_policy_map,
    default_controls_path,
    load_control_policy_map,
    load_controls,
    save_state,
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CANNOT = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="guardrail-controls",
        description="server-side guardrail PolicyControl surface (issue #343).",
    )
    parser.add_argument("--controls", default=None, help="controls registry YAML")
    parser.add_argument("--state", default=None, help="control-state JSON file")
    parser.add_argument("--audit", default=None, help="append-only audit JSONL file")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list the control registry")

    get = sub.add_parser("get", help="show one control as JSON")
    get.add_argument("control_id")

    toggle = sub.add_parser("toggle", help="flip one control (one audit record)")
    toggle.add_argument("control_id")
    target = toggle.add_mutually_exclusive_group()
    target.add_argument("--on", action="store_true", help="set the control ON")
    target.add_argument("--off", action="store_true", help="set the control OFF")
    toggle.add_argument("--actor", default="cli", help="actor recorded on the audit record")
    toggle.add_argument("--reason", default="", help="reason recorded on the audit record")

    report = sub.add_parser("check-report", help="report 246/446 per control")
    report.add_argument("--json", action="store_true", help="print the report as JSON")

    selftest = sub.add_parser("self-test", help="run the control invariants")
    selftest.add_argument(
        "--mutate",
        choices=("default-on",),
        default=None,
        help="provoke the named negative control only (must be refused)",
    )
    return parser


def _registry_path(args: argparse.Namespace) -> Path:
    """Registry path (explicit --controls, else the shipped controls.yaml)."""
    return Path(args.controls) if args.controls else default_controls_path()


def _state_path(args: argparse.Namespace, controls_path: Path) -> Path:
    return Path(args.state) if args.state else controls_path.with_name("controls.state.json")


def _audit_path(args: argparse.Namespace, controls_path: Path) -> Path:
    return Path(args.audit) if args.audit else controls_path.with_name("controls.audit.jsonl")


def cmd_list(args: argparse.Namespace) -> int:
    resolved = _registry_path(args)
    control_set = build_control_set(resolved, state_path=_state_path(args, resolved))
    print(f"{'id':<22} {'enabled':<8} {'mode':<6} {'status':<10} owner")
    for state in control_set.states():
        enabled = "on" if state.enabled else "off"
        print(
            f"{state.control.id:<22} {enabled:<8} {state.control.mode:<6} "
            f"{state.status} {state.status_name:<6} {state.control.owner}"
        )
    return EXIT_OK


def cmd_get(args: argparse.Namespace) -> int:
    resolved = _registry_path(args)
    control_set = build_control_set(resolved, state_path=_state_path(args, resolved))
    for state in control_set.states():
        if state.control.id == args.control_id:
            print(json.dumps({**state.control.to_dict(), **state.to_dict()}, indent=2, sort_keys=True))
            return EXIT_OK
    print(f"get: unknown control {args.control_id!r}", file=sys.stderr)
    return EXIT_FAIL


def cmd_toggle(args: argparse.Namespace) -> int:
    resolved = _registry_path(args)
    state_path = _state_path(args, resolved)
    audit = JsonlControlAuditLog(_audit_path(args, resolved))
    target = True if args.on else False if args.off else None
    try:
        control_set = build_control_set(resolved, state_path=state_path)
        record = control_set.toggle(
            args.control_id, target, actor=args.actor, audit_log=audit, reason=args.reason
        )
    except UnknownControlError as exc:
        print(f"toggle: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except ControlError as exc:
        print(f"toggle: {exc}", file=sys.stderr)
        return EXIT_FAIL
    save_state(state_path, {cid: control_set.is_enabled(cid) for cid in control_set.ids()})
    print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    return EXIT_OK


def cmd_check_report(args: argparse.Namespace) -> int:
    resolved = _registry_path(args)
    try:
        control_set = build_control_set(resolved, state_path=_state_path(args, resolved))
    except ControlError as exc:
        print(f"check-report: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT
    rows = [
        {
            "id": state.control.id,
            "status": state.status,
            "status_name": state.status_name,
            "enabled": state.enabled,
        }
        for state in control_set.states()
    ]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        for row in rows:
            print(f"{row['id']:<22} {row['status']} {row['status_name']}")
    if control_set.all_default_off():
        return EXIT_OK
    print("check-report: NOT-OK — one or more controls are enabled (not default-OFF)", file=sys.stderr)
    return EXIT_FAIL


def _selftest_checks() -> list[tuple[str, bool, str]]:
    """Return (name, ok, detail) for every invariant this lane asserts."""
    results: list[tuple[str, bool, str]] = []

    controls = load_controls(None)
    default_on = [control.id for control in controls if control.default_enabled]
    results.append(
        ("every control defaults OFF", not default_on, f"{len(controls)} control(s)")
    )

    fresh = build_control_set(None)
    results.append(
        ("a fresh control set is all-OFF", fresh.all_default_off(), f"{len(fresh)} control(s)")
    )

    unknown_refused = False
    detail = ""
    try:
        from controls.audit import InMemoryControlAuditLog

        fresh.toggle("no-such-control", True, actor="self-test", audit_log=InMemoryControlAuditLog())
    except UnknownControlError as exc:
        unknown_refused = True
        detail = str(exc)
    results.append(("an unknown control is refused", unknown_refused, detail))

    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "state.json"
        audit_path = Path(tmp) / "audit.jsonl"
        writer = JsonlControlAuditLog(audit_path)
        first = controls[0].id
        control_set = build_control_set(None, state_path=state_path)
        control_set.toggle(first, True, actor="self-test", audit_log=writer)
        save_state(state_path, {cid: control_set.is_enabled(cid) for cid in control_set.ids()})

        reader_audit = JsonlControlAuditLog(audit_path)
        reader_set = build_control_set(None, state_path=state_path)
        one_record = len(reader_audit) == 1
        second_reader = reader_set.is_enabled(first) is True
        results.append(
            ("a toggle writes exactly one audit record", one_record, f"{len(reader_audit)} record(s)")
        )
        results.append(
            ("a second reader sees the flip", second_reader, f"{first}={reader_set.is_enabled(first)}")
        )

    closed = (
        is_status(STATUS_PASSED)
        and is_status(STATUS_BLOCKED)
        and not is_status(999)
        and not is_status("246")
        and GUARDRAIL_STATUS == frozenset({STATUS_PASSED, STATUS_BLOCKED})
        and status_name(STATUS_PASSED) == "PASSED"
        and status_name(STATUS_BLOCKED) == "BLOCKED"
    )
    refused_unknown_code = False
    try:
        status_name(999)
    except ControlError:
        refused_unknown_code = True
    results.append(
        ("the 246/446 vocabulary is closed", closed and refused_unknown_code,
         f"refuses 999={refused_unknown_code}")
    )

    missing = cross_check_policy_map(controls, load_control_policy_map())
    results.append(
        ("every guardrail control is in the reused CONTROL_POLICY_MAP",
         not missing, f"missing={list(missing)}")
    )

    negative = False
    negative_detail = ""
    try:
        assert_refuses_default_on()
        negative = True
        negative_detail = "a control that ships ON is refused"
    except AssertionError as exc:
        negative_detail = str(exc)
    results.append(("negative control: a control that ships ON is refused", negative, negative_detail))

    return results


def cmd_self_test(args: argparse.Namespace) -> int:
    if args.mutate == "default-on":
        try:
            assert_refuses_default_on()
        except AssertionError as exc:
            print(f"self-test --mutate default-on: FAIL — {exc}", file=sys.stderr)
            return EXIT_FAIL
        print("self-test --mutate default-on: OK — a control that ships ON is refused")
        return EXIT_OK

    results = _selftest_checks()
    failed = 0
    for name, ok, detail in results:
        flag = "OK" if ok else "FAIL"
        suffix = f" ({detail})" if detail else ""
        print(f"  {flag:<4} {name}{suffix}")
        if not ok:
            failed += 1
    if failed:
        print(f"self-test: FAIL — {failed} of {len(results)} invariant(s) did not hold", file=sys.stderr)
        return EXIT_FAIL
    print(f"self-test: OK — {len(results)} invariant(s) hold")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "get": cmd_get,
        "toggle": cmd_toggle,
        "check-report": cmd_check_report,
        "self-test": cmd_self_test,
    }
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse rejects unknown commands
        parser.error(f"unknown command {args.command!r}")
        return EXIT_FAIL  # pragma: no cover
    try:
        return handler(args)
    except ControlError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
