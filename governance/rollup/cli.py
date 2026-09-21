"""Operator CLI for the enterprise/GDC roll-up (issue #151).

---knowledge---
module_id: governance.rollup.cli
system: governance
app: rollup
solution_class: pattern
patterns: [honesty-tri-state]
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [cmd_project, cmd_validate, build_parser, main]
invariants: ""
gotchas: ""
related: ["#151"]
do_not_duplicate: null
---knowledge---

    python3 governance/rollup/cli.py project        # the org view (pilot inputs)
    python3 governance/rollup/cli.py project --org … --inventory-dir …
    python3 governance/rollup/cli.py validate       # declarations only

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

``project`` reports the tri-state of the *rolled-up view*: an over-ceiling SME
or an isolation violation is NOT-OK, and an unreadable or unassessable input set
is CANNOT-ASSESS — which is why a run that could not read everything can never
exit 0. ``validate`` reports on the declarations alone: 1 means a declaration is
definitely invalid, 2 means validation itself could not run.

Both print computed values only. There is no narration path: every number comes
from ``model.summarise``/``to_dict``, which read the projected report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inputs import (  # noqa: E402
    ORG_RELPATH,
    SCHEMA_RELPATH,
    PyYamlMissing,
    load_inputs,
    with_problems,
)
from model import EXIT_CODES, STATUS_CANNOT_ASSESS, STATUS_NOT_OK, STATUS_OK, project, summarise  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_DIR = ROOT / "governance" / "rollup" / "pilot" / "inventory"


def _paths(args: argparse.Namespace):
    org = Path(args.org) if args.org else ROOT / ORG_RELPATH
    inventory_dir = Path(args.inventory_dir) if args.inventory_dir else DEFAULT_INVENTORY_DIR
    schema = Path(args.schema) if args.schema else ROOT / SCHEMA_RELPATH
    return org, inventory_dir, schema


def _build(args: argparse.Namespace):
    org_path, inventory_dir, schema_path = _paths(args)
    loaded = load_inputs(org_path, inventory_dir, schema_path)
    if loaded.org is None:
        return loaded, None
    report = project(
        loaded.org,
        loaded.fleets,
        inputs=loaded.inputs,
        schema_path=loaded.schema_path,
        schema_digest=loaded.schema_digest,
    )
    return loaded, with_problems(report, loaded.problems)


def cmd_project(args: argparse.Namespace) -> int:
    loaded, report = _build(args)
    if report is None:
        for problem in loaded.problems:
            print(
                "rollup: CANNOT-ASSESS — %s %s: %s"
                % (problem.kind, problem.path, "; ".join(problem.messages)),
                file=sys.stderr,
            )
        return EXIT_CODES[STATUS_CANNOT_ASSESS]

    if args.json:
        print(report.to_json())
    else:
        for line in summarise(report):
            print(line)
        print("rollup: status=%s (a projection of the declarations above)" % report.status)
    return report.exit_code


def cmd_validate(args: argparse.Namespace) -> int:
    loaded, report = _build(args)
    if loaded.schema_digest == "":
        for problem in loaded.problems:
            print(
                "rollup: CANNOT-ASSESS — %s %s: %s"
                % (problem.kind, problem.path, "; ".join(problem.messages)),
                file=sys.stderr,
            )
        return EXIT_CODES[STATUS_CANNOT_ASSESS]

    if args.json:
        payload = {
            "schema": "ao.rollup/input-validation-v1",
            "valid": not loaded.problems,
            "org": str(_paths(args)[0]) if loaded.org is not None else None,
            "repos_declared": len(loaded.org.all_repos) if loaded.org else 0,
            "repos_with_inventory": len(loaded.fleets),
            "problems": [
                {"kind": p.kind, "path": p.path, "messages": list(p.messages)}
                for p in loaded.problems
            ],
            "inputs": [
                {"kind": k, "path": p, "sha256": d} for k, p, d in loaded.inputs
            ],
        }
        import json

        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for problem in loaded.problems:
            print(
                "rollup: INVALID — %s %s: %s"
                % (problem.kind, problem.path, "; ".join(problem.messages))
            )
        if not loaded.problems:
            print(
                "rollup: declarations valid (%d repo(s) declared, %d inventory file(s))"
                % (len(loaded.org.all_repos) if loaded.org else 0, len(loaded.fleets))
            )
    if loaded.problems:
        return EXIT_CODES[STATUS_NOT_OK]
    return EXIT_CODES[STATUS_OK]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rollup", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, func in (("project", cmd_project), ("validate", cmd_validate)):
        cmd = sub.add_parser(name)
        cmd.add_argument("--org", help="org declaration (default: the committed pilot)")
        cmd.add_argument(
            "--inventory-dir", help="directory of per-repo fleet inventories (default: the pilot)"
        )
        cmd.add_argument("--schema", help="input contract (default: governance/rollup/schema.yaml)")
        cmd.add_argument("--json", action="store_true", help="emit the report as JSON")
        cmd.set_defaults(func=func)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PyYamlMissing as exc:
        print("rollup: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CODES[STATUS_CANNOT_ASSESS]


if __name__ == "__main__":
    sys.exit(main())
