#!/usr/bin/env python3
"""CLI for the hermes integration adapter (issue #942).

Three verbs, matching the three things an operator needs from the seam:

* ``project`` — build and print the offline canonical projection (JSON). No
  network: it is a pure function of the tree.
* ``check``   — validate the projection against the inline contract and the
  cross-source consistency rules; tri-state exit (0 OK / 1 NOT-OK /
  2 CANNOT-ASSESS). This is what the gate calls.
* ``probe``   — the live path: ``GET /health`` over ``HttpTransport``. Implemented,
  and deliberately **never** run by the gate (the service is deployable-not-running).

Run as ``python3 integrations/hermes/cli.py <verb>`` from the repo root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations.hermes import mapping as mapping_mod  # noqa: E402


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def cmd_project(args: argparse.Namespace) -> int:
    root = _root(args)
    projection = mapping_mod.build_projection(root)
    print(mapping_mod.canonical_document(projection))
    print(
        "# sha256: %s" % mapping_mod.canonical_sha(projection),
        file=sys.stderr,
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = _root(args)
    findings, projection = mapping_mod.check_projection(root)
    if findings:
        print(
            "check: NOT-OK — %d finding(s) in the hermes projection"
            % len(findings),
            file=sys.stderr,
        )
        for finding in findings:
            print("  FAIL  %s" % finding, file=sys.stderr)
        return 1
    print(
        "check: OK — hermes projection conforms (sha256=%s)"
        % mapping_mod.canonical_sha(projection)
    )
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from integrations.hermes import client as client_mod  # noqa: E402 - lazy: network path only

    client = client_mod.HermesClient(args.base_url)
    try:
        response = client.health()
    except Exception as exc:  # noqa: BLE001 - probe is best-effort by design
        print("probe: UNREACHABLE — %s" % exc, file=sys.stderr)
        return 1
    print(
        "probe: %s /health -> HTTP %s" % (args.base_url, response.status)
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-adapter",
        description="hermes integration adapter (issue #942, ADR-0012)",
    )
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    sub = parser.add_subparsers(dest="verb", required=True)

    project = sub.add_parser("project", help="print the offline canonical projection")
    project.set_defaults(func=cmd_project)

    check = sub.add_parser("check", help="validate the projection (tri-state)")
    check.set_defaults(func=cmd_check)

    probe = sub.add_parser("probe", help="live GET /health (never run by the gate)")
    probe.add_argument("--base-url", default="http://localhost:9501",
                       help="routing-service server root (default: the declared port)")
    probe.set_defaults(func=cmd_probe)
    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
