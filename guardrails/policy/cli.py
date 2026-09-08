"""Command-line interface for the policy-as-code + gate framework.

Subcommands (exit codes are honest and documented):

``validate``
    Startup validation gate over a bundle directory (default: the shipped
    ``bundles/platform`` examples).  Exit 0 = every policy valid; exit 1 =
    one or more policies failed schema/semantic validation (an invalid policy
    fails the deploy, never at runtime — issue #26 acceptance #1).

``evaluate ACTION``
    Evaluate one action against the bundle + controls and print the
    BLOCK/WARN/LOG decision with its structured evidence as JSON.  Exit 0 =
    allowed (WARN/LOG); exit 2 = BLOCKED; exit 1 = the gate itself errored.
    A BLOCKED exit is what a caller gates on.

``controls``
    List the controls registry (id, name, enabled, mode) — the toggleable,
    default-OFF control surface (issue #26 acceptance #3).

Defaults point at the shipped examples, so ``python policy/cli.py validate``
works from the repository root without arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# -- import bootstrap --------------------------------------------------------
# `guardrails/` has no __init__.py (like `engine/`), so the `policy` package
# is imported with `guardrails/` on sys.path.  When run as a script from the
# repo root this makes `from policy import ...` resolve; when run as
# `python3 -m policy.cli` from within guardrails/ the path is already present.
_here = os.path.dirname(os.path.abspath(__file__))
_guardrails_root = os.path.dirname(_here)
if _guardrails_root not in sys.path:
    sys.path.insert(0, _guardrails_root)

from policy import __version__  # noqa: E402
from policy.controls import ControlRegistry  # noqa: E402
from policy.decision import DecisionLevel  # noqa: E402
from policy.startup import (  # noqa: E402
    default_bundle_dir,
    default_controls_file,
    validate_paths,
)

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_BLOCKED = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="policy-gate",
        description="agent-orchestrator policy-as-code + gate engine (issue #26).",
    )
    parser.add_argument("--version", action="version", version=f"policy-gate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="startup validation gate over a bundle")
    validate.add_argument("paths", nargs="*",
                          help="bundle directory/file (default: --bundle or shipped examples)")
    validate.add_argument("--bundle", dest="bundle_dir", default=default_bundle_dir(),
                          help="bundle directory/file to validate (default: shipped examples)")
    validate.add_argument("--controls", default=default_controls_file(),
                          help="controls registry YAML (default: shipped controls.yaml)")

    evaluate = sub.add_parser("evaluate", help="evaluate one action (prints JSON decision)")
    evaluate.add_argument("action", help="action name, e.g. model.call, tool.use, egress.send")
    evaluate.add_argument("--bundle", dest="bundle_dir", default=default_bundle_dir())
    evaluate.add_argument("--controls", default=default_controls_file())
    evaluate.add_argument("--subject", default=None)
    evaluate.add_argument("--tenant", default=None)
    evaluate.add_argument("--context", default="{}", help="action context as JSON")
    evaluate.add_argument("--uncovered", default="block",
                          choices=("block", "warn", "log"),
                          help="decision when no active policy governs the action "
                               "(default: block — fail closed)")

    controls = sub.add_parser("controls", help="list the controls registry")
    controls.add_argument("--controls", default=default_controls_file())
    return parser


def _load_controls(path: str | None) -> ControlRegistry | None:
    if not path:
        return None
    return ControlRegistry.load_yaml(path)


def cmd_validate(args: argparse.Namespace) -> int:
    paths = args.paths if args.paths else [args.bundle_dir]
    controls = _load_controls(args.controls)
    report = validate_paths(paths, controls=controls)
    print(f"guardrails/policy CLI {__version__} — startup validation")
    print(f"  bundle: {', '.join(paths)}")
    if args.controls:
        print(f"  controls: {args.controls} ({len(controls) if controls else 0} registered)")
    print(f"  files: {report.file_count}, policies: {report.policy_count}")
    if report.ok:
        print("  valid: yes")
        return EXIT_OK
    print("  valid: NO", file=sys.stderr)
    for error in report.errors:
        print(f"  - {error}", file=sys.stderr)
    return EXIT_INVALID


def cmd_evaluate(args: argparse.Namespace) -> int:
    controls = _load_controls(args.controls)
    try:
        context = json.loads(args.context)
    except ValueError as exc:
        print(f"evaluate: --context is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_INVALID
    if not isinstance(context, dict):
        print("evaluate: --context must be a JSON object", file=sys.stderr)
        return EXIT_INVALID

    from policy.startup import build_engine

    engine = build_engine([args.bundle_dir], controls=controls, uncovered_decision=args.uncovered)
    result = engine.evaluate(args.action, subject=args.subject, tenant=args.tenant, context=context)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if result.blocked:
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_controls(args: argparse.Namespace) -> int:
    controls = _load_controls(args.controls)
    if controls is None:
        print("controls: no registry file provided/found", file=sys.stderr)
        return EXIT_INVALID
    print(f"{'id':<28} {'enabled':<9} {'mode':<7} name")
    for control in controls.all():
        enabled = "on" if control.enabled else "off"
        print(f"{control.id:<28} {enabled:<9} {control.mode:<7} {control.name}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "evaluate":
        return cmd_evaluate(args)
    if args.command == "controls":
        return cmd_controls(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return EXIT_INVALID  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
