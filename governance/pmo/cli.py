#!/usr/bin/env python3
"""``governance/pmo/cli.py`` — the PMO layer's machine surface (issue #403).

Six subcommands, each a **derived query over the ticket graph** (ADR-0014,
issue #401) — never a store, never a second source of ``status``:

```bash
python3 governance/pmo/cli.py deps    # blocked_by + goal edges
python3 governance/pmo/cli.py lanes   # owner × lane occupancy
python3 governance/pmo/cli.py report  # tickets by goal / status / owner
python3 governance/pmo/cli.py raid    # R / A / I / D + dependency edges
python3 governance/pmo/cli.py aging   # what has been waiting, tiered
python3 governance/pmo/cli.py gates   # review-gate state + escalation rung (#635)
```

Every subcommand runs **offline** over the committed projection and exits
tri-state:

* ``0`` OK — the view was computed and reports nothing.
* ``1`` NOT-OK — the view was computed and reports a finding (an owner-less
  live risk, an aging item nobody owns, an orphan dependency edge, a rollup that
  misses a board issue).
* ``2`` CANNOT-ASSESS — the graph itself could not be built (an unreadable
  board/ledger, a projection that refuses to build, no timestamp to anchor an
  age). **CANNOT-ASSESS is never reported as a pass.**

``--check`` is the gate mode: it additionally reconciles the freshly derived view
against a previously rendered one (``--against FILE``), which is how a ticket
removed from the graph but left behind in a cached view is caught **by name**.
The PMO writes no cache of its own — it must merely be able to *prove* that a
view it is handed is not one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from graph import CannotAssess, Graph, load
from views import VIEWS, Finding, View, reconcile


def _print_human(view: View) -> None:
    print(f"pmo {view.name}: clock={view.clock or 'unknown'}")
    print(json.dumps(view.document, indent=2, sort_keys=True))
    for note in view.notes:
        print(f"  NOTE  {note}")


def _load_against(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CannotAssess(f"view to check against is missing: {path}") from exc
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"view to check against is unreadable: {path} ({exc})") from exc


def _fail(findings) -> int:
    for finding in findings:
        print(f"  FAIL  {finding.render()}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pmo",
        description="PMO views derived from the ticket graph (issue #403).",
    )
    parser.add_argument("view", choices=sorted(VIEWS), help="the view to derive")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="emit the view as JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="gate mode: report findings as NOT-OK and reconcile --against a saved view",
    )
    parser.add_argument(
        "--against",
        default=None,
        metavar="FILE",
        help="a previously rendered view to reconcile this one against",
    )
    parser.add_argument(
        "--now",
        default=None,
        metavar="ISO",
        help="override the clock (default: the latest timestamp in the committed inputs)",
    )
    args = parser.parse_args(argv)

    try:
        graph: Graph = load(args.root)
        if args.now:
            graph.clock = args.now

        view = VIEWS[args.view](graph)
        findings = list(view.findings)
        if args.check:
            # A view is a projection, so deriving it twice over one graph must be
            # byte-identical: a render that depended on iteration order or the
            # wall clock would be a second source of truth wearing a view's name.
            again = VIEWS[args.view](graph)
            if again.text() != view.text():
                findings.append(
                    Finding(
                        "view-nondeterministic",
                        view.name,
                        "two derivations over one graph differ",
                    )
                )
        if args.against:
            findings.extend(reconcile(view, _load_against(args.against)))

        if args.json:
            # The JSON document is the whole of stdout: a caller redirects it and
            # checks it, so the summary must not corrupt it.
            print(json.dumps(view.document, indent=2, sort_keys=True))
        else:
            _print_human(view)

        if findings:
            return _fail(findings)
        print(
            f"pmo {view.name}: OK — no finding over {len(graph.tickets)} ticket(s)",
            file=sys.stderr if args.json else sys.stdout,
        )
        return 0
    except CannotAssess as exc:
        print(f"pmo {args.view}: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
