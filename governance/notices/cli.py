"""Notice verbs (issue #1269): the surface a runtime's transport or the director drives.

---knowledge---
module_id: governance.notices.cli
system: governance
app: notices
solution_class: enterprise
patterns: [provoked-negative-control, honesty-tri-state]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [cmd_runtimes, cmd_publish, cmd_ack, cmd_evaluate, cmd_ledger, cmd_controls, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1268", "#1269"]
do_not_duplicate: null
---knowledge---

    python3 governance/notices/cli.py runtimes            # who is registered, and why
    python3 governance/notices/cli.py publish --id <id> --subject <text> --body <text>
    python3 governance/notices/cli.py ack --notice <id> --runtime <id> --evidence <what was read>
    python3 governance/notices/cli.py evaluate            # what is owed, what is outstanding

`evaluate` writes nothing, so the gate of record (`scripts/check-notice-acks.sh`)
can drive it against the live tree and against a planted scratch fleet without
either run touching the other.

Exit-code contract (the repo's honesty tri-state):
  0 OK / 1 NOT-OK (a refusal, by name) / 2 CANNOT-ASSESS (never a pass).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.notices import controls as notice_controls
from governance.notices import ledger as notice_ledger
from governance.notices.notice_records import (  # noqa: E402
    NOTICES_DIRNAME,
    NoticeError,
    acknowledge,
    evaluate,
    publish,
)
from governance.notices.runtime_registry import (  # noqa: E402
    RegistryUnavailable,
    describe,
    registered_runtimes,
)

CANNOT_ASSESS = 2


def _fleet_dir(args: argparse.Namespace) -> Path:
    if args.fleet:
        return Path(args.fleet)
    from_env = os.environ.get("AO_FLEET_DIR")
    if from_env:
        return Path(from_env)
    return Path(args.root) / ".fleet"


def _cannot_assess(message: str) -> int:
    print("notice-acks: CANNOT-ASSESS -- %s" % message, file=sys.stderr)
    return CANNOT_ASSESS


def cmd_runtimes(args: argparse.Namespace) -> int:
    """Print the derived registered-runtime set: no list is typed anywhere."""
    try:
        report = describe(args.root)
    except RegistryUnavailable as exc:
        return _cannot_assess(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    runtimes = report["runtimes"]
    print("notice-acks: runtimes=%d roles=%d" % (len(runtimes), len(report["roles"])))
    for runtime in runtimes:
        print("  runtime %s (provider %s, transport %s)"
              % (runtime["id"], runtime["provider"], runtime["transport"]))
    for role in report["roles"]:
        print("  role %s (registered identity, no transport: owes no ack)" % role)
    for release in report["pack_releases"]:
        print("  read %s" % release)
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish a standing notice and fan a copy out to every registered runtime."""
    body = args.body
    if args.body_file:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            return _cannot_assess("%s cannot be read: %s" % (args.body_file, exc))
    fleet = _fleet_dir(args)
    try:
        path = publish(
            args.root,
            fleet,
            notice_id=args.id,
            subject=args.subject,
            body=body or "",
            issued_by=args.issued_by,
            status=args.status,
            refs=tuple(args.ref or ()),
        )
    except RegistryUnavailable as exc:
        return _cannot_assess(str(exc))
    except NoticeError as exc:
        print("notice-acks: FAIL -- %s" % exc, file=sys.stderr)
        return 1
    runtimes = registered_runtimes(args.root)
    print("notice-acks: published %s to %d registered runtime(s)" % (args.id, len(runtimes)))
    print("  record %s" % path)
    for runtime in runtimes:
        print("  fanout %s -> %s (transport %s)"
              % (runtime.id, runtime.fanout, runtime.transport))
    print("  acks land in %s/<id>/ack-<runtime>.json" % (Path(fleet) / NOTICES_DIRNAME))
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    """Record one runtime's acknowledgement of one standing notice."""
    try:
        path = acknowledge(
            args.root,
            _fleet_dir(args),
            notice_id=args.notice,
            runtime_id=args.runtime,
            evidence=args.evidence,
            transport=args.transport,
        )
    except RegistryUnavailable as exc:
        return _cannot_assess(str(exc))
    except NoticeError as exc:
        print("notice-acks: FAIL -- %s" % exc, file=sys.stderr)
        return 1
    print("notice-acks: acked %s by %s" % (args.notice, args.runtime))
    print("  record %s" % path)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Report what the registry requires and what is outstanding. Writes nothing."""
    report = evaluate(args.root, _fleet_dir(args))
    if args.json:
        print(json.dumps(
            {
                "root": report.root,
                "fleet": report.fleet,
                "runtimes": [runtime.as_dict() for runtime in report.runtimes],
                "roles": list(report.roles),
                "notices": list(report.notices),
                "findings": [str(finding) for finding in report.findings],
                "cannot_assess": report.cannot_assess,
                "rc": report.rc(),
            },
            indent=2,
            sort_keys=True,
        ))
    else:
        for line in report.lines():
            if report.rc() == 0:
                print(line)
            else:
                print(line, file=sys.stderr)
    return report.rc()


def cmd_ledger(args: argparse.Namespace) -> int:
    """Print the ledger, or verify its chain and the digests it recorded."""
    fleet = _fleet_dir(args)
    if args.verify:
        findings = notice_ledger.verify(fleet)
        print("notice-acks: ledger %s" % notice_ledger.ledger_path(fleet))
        for finding in findings:
            print("  FAIL    %s" % finding.line(), file=sys.stderr)
        if findings:
            print("notice-acks: FAIL (%d violation(s))" % len(findings), file=sys.stderr)
            return 1
        print("notice-acks: OK -- the ledger chains and every record matches its digest")
        return 0
    try:
        entries = notice_ledger.read(fleet)
    except notice_ledger.LedgerIntegrityError as exc:
        print("notice-acks: FAIL -- %s" % exc, file=sys.stderr)
        return 1
    print("notice-acks: ledger entries=%d" % len(entries))
    for entry in entries:
        print("  seq=%s %s %s %s" % (
            entry.get("seq"), entry.get("event"), entry.get("notice"),
            entry.get("runtime") or "-"))
    return 0


def cmd_controls(args: argparse.Namespace) -> int:
    """Hold the declaration to the code it describes and to the provocations it names."""
    path = args.controls or str(Path(args.root) / notice_controls.CONTROLS_RELPATH)
    try:
        declared = notice_controls.load(path)
    except notice_controls.ControlsUnavailable as exc:
        return _cannot_assess(str(exc))
    findings = notice_controls.check(declared, args.root)
    print("notice-acks: controls %s" % declared.path)
    for finding in findings:
        print("  FAIL    %s" % finding.line(), file=sys.stderr)
    if findings:
        print("notice-acks: FAIL (%d violation(s))" % len(findings), file=sys.stderr)
        return 1
    print("notice-acks: OK -- %d refusal(s) declared, each armed, and the vocabulary mirrors the code"
          % len(declared.refusals))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/notices/cli.py",
        description="Standing notices and their acks (issue #1269, EPIC #1268).",
    )
    parser.add_argument("--root", default=str(ROOT),
                        help="the tree whose registry is read (default: this checkout)")
    parser.add_argument("--fleet", default="",
                        help="the fleet runtime dir holding notices/ (default: $AO_FLEET_DIR or <root>/.fleet)")
    verbs = parser.add_subparsers(dest="verb", required=True)

    runtimes = verbs.add_parser("runtimes", help="print the derived registered-runtime set")
    runtimes.add_argument("--json", action="store_true")
    runtimes.set_defaults(func=cmd_runtimes)

    publish_parser = verbs.add_parser("publish", help="publish a notice and fan it out")
    publish_parser.add_argument("--id", required=True)
    publish_parser.add_argument("--subject", required=True)
    publish_parser.add_argument("--body", default="")
    publish_parser.add_argument("--body-file", default="")
    publish_parser.add_argument("--issued-by", default="director")
    publish_parser.add_argument("--status", default="standing", choices=("standing", "withdrawn"))
    publish_parser.add_argument("--ref", action="append", default=[])
    publish_parser.set_defaults(func=cmd_publish)

    ack_parser = verbs.add_parser("ack", help="record one runtime's acknowledgement")
    ack_parser.add_argument("--notice", required=True)
    ack_parser.add_argument("--runtime", required=True)
    ack_parser.add_argument("--evidence", required=True,
                            help="what the runtime read (an ack without evidence is refused)")
    ack_parser.add_argument("--transport", default="")
    ack_parser.set_defaults(func=cmd_ack)

    evaluate_parser = verbs.add_parser("evaluate", help="report outstanding acks (read-only)")
    evaluate_parser.add_argument("--json", action="store_true")
    evaluate_parser.set_defaults(func=cmd_evaluate)

    ledger_parser = verbs.add_parser("ledger", help="print the notice ledger, or verify its chain")
    ledger_parser.add_argument("--verify", action="store_true")
    ledger_parser.set_defaults(func=cmd_ledger)

    controls_parser = verbs.add_parser(
        "controls", help="hold the declared vocabulary to the code and to its provocations"
    )
    controls_parser.add_argument("--controls", default="",
                                 help="the declaration to read (default: the shipped controls.yaml)")
    controls_parser.set_defaults(func=cmd_controls)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except RegistryUnavailable as exc:
        return _cannot_assess(str(exc))


if __name__ == "__main__":
    sys.exit(main())
