#!/usr/bin/env python3
"""CLI for the routine projection (issue #418).

Verbs:

* ``project``  — print every routine derived from ``fleet/cron.py`` (canonical
  JSON), refusing a schedule the code does not declare and reporting a scheduled
  marker no routine claims.
* ``verify``   — re-derive twice (determinism), re-read the schedule and confirm
  every entry maps to exactly one routine, and report the findings. This is what
  the gate calls.
* ``registry`` — print the routine registry (identity + owner + anchor only —
  never a schedule). The gate dumps it, mutates a copy and hands it back with
  ``--registry`` to provoke an owner-less routine.

Tri-state exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. The adapter is read-only:
none of these verbs writes the tree.

Run as ``python3 integrations/paperclip/adapters/routines/cli.py <verb>`` from
the repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from integrations.paperclip.adapters.routines import projection as projection_mod  # noqa: E402
from integrations.paperclip.adapters.routines.model import (  # noqa: E402
    CannotAssess,
    ROUTINES,
    RoutineRefused,
    specs_from_records,
)
from integrations.paperclip.adapters.routines.schedule import read_schedule  # noqa: E402


def _root(args: argparse.Namespace) -> Path:
    if args.root:
        return Path(args.root).resolve()
    return Path(__file__).resolve().parents[4]


def _pmo_root(args: argparse.Namespace, root: Path) -> Path:
    return Path(args.pmo_root).resolve() if args.pmo_root else root


def _registry(args: argparse.Namespace):
    """The routine registry to project with — the built-in one, or a copy."""
    if not args.registry:
        return None
    path = Path(args.registry)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CannotAssess(f"registry is missing: {path}") from exc
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"registry is unreadable: {path} ({exc})") from exc
    if isinstance(payload, dict):
        payload = payload.get("routines")
    if not isinstance(payload, list):
        raise RoutineRefused(
            "bad-registry",
            f"{path} does not carry a list of routine records",
        )
    return specs_from_records(payload)


def _report_findings(findings) -> None:
    print(f"  NOT-OK — {len(findings)} finding(s)", file=sys.stderr)
    for finding in findings:
        print(f"    FAIL  {finding.line()}", file=sys.stderr)


def cmd_project(args: argparse.Namespace) -> int:
    root = _root(args)
    view = projection_mod.project(root, pmo_root=_pmo_root(args, root), specs=_registry(args))
    sys.stdout.write(projection_mod.render(view))
    for note in view.notes:
        print(f"  NOTE  {note}", file=sys.stderr)
    if view.findings:
        _report_findings(view.findings)
        return 1
    print(f"project: OK — {len(view.routines)} routine(s) projected from fleet/cron.py", file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = _root(args)
    pmo_root = _pmo_root(args, root)
    specs = _registry(args)
    if not projection_mod.deterministic(root, pmo_root=pmo_root):
        print("verify: NOT-OK — two derivations over one revision differ", file=sys.stderr)
        return 1
    view = projection_mod.project(root, pmo_root=pmo_root, specs=specs)
    # Re-read the schedule and confirm every entry still maps to exactly one
    # routine — the drift check, restated over the freshly read code.
    schedule = read_schedule(root)
    seen = [routine.marker for routine in view.routines]
    drifted = sorted(
        {entry.marker for entry in schedule.entries if seen.count(entry.marker) != 1}
    )
    for marker in drifted:
        print(
            f"  FAIL  drift: scheduled marker {marker!r} does not map to exactly one routine",
            file=sys.stderr,
        )
        return 1
    if view.findings:
        _report_findings(view.findings)
        return 1
    print(
        f"verify: OK — {len(view.routines)} routine(s), {len(schedule.entries)} scheduled "
        f"entr(ies) each projected once, projection deterministic"
    )
    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    document = {
        "routines": [spec.to_dict() for spec in ROUTINES],
        "note": "identity, owner, lane and anchor only — the schedule lives in fleet/cron.py",
    }
    sys.stdout.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperclip-routines", description=__doc__)
    parser.add_argument("--root", default="", help="tree to read the schedule from (default: repo root)")
    parser.add_argument(
        "--pmo-root",
        default="",
        help="tree to read the PMO graph from (default: --root)",
    )
    parser.add_argument(
        "--registry",
        default="",
        metavar="FILE",
        help="an alternate routine registry (JSON) — identity/owner only, never a schedule",
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("project", help="print the derived routines").set_defaults(func=cmd_project)
    sub.add_parser("verify", help="re-derive and re-check every routine").set_defaults(func=cmd_verify)
    sub.add_parser("registry", help="print the routine registry").set_defaults(func=cmd_registry)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RoutineRefused as exc:
        print(f"  FAIL  {exc.reason}: {exc.subject or '-'} — {exc.detail}", file=sys.stderr)
        return 1
    except CannotAssess as exc:
        print(f"routines: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
