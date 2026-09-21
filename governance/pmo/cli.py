#!/usr/bin/env python3
"""``governance/pmo/cli.py`` — the PMO layer's machine surface (issue #403 + follow-on).

Eight subcommands, each a **derived query over the ticket graph** (ADR-0014,
issue #401) — never a store, never a second source of ``status``:

```bash
python3 governance/pmo/cli.py deps       # blocked_by + goal edges
python3 governance/pmo/cli.py lanes      # owner × lane occupancy
python3 governance/pmo/cli.py report     # tickets by goal / status / owner
python3 governance/pmo/cli.py raid       # R / A / I / D + dependency edges
python3 governance/pmo/cli.py aging      # what has been waiting, tiered
python3 governance/pmo/cli.py gates      # review-gate state + escalation rung (#635)
python3 governance/pmo/cli.py priority   # a single explainable priority order
python3 governance/pmo/cli.py dispatch   # priority order -> agent/lane/tier plan
```

Every subcommand runs **offline by default** over the committed projection and
``governance/pmo/policy.yaml``, and exits tri-state:

* ``0`` OK — the view was computed and reports nothing.
* ``1`` NOT-OK — the view was computed and reports a finding (an owner-less
  live risk, an aging item nobody owns, a dispatch plan with a lane collision
  or a silently dropped unowned risk).
* ``2`` CANNOT-ASSESS — the graph or policy could not be built (an unreadable
  board/ledger, a malformed ``policy.yaml``). **CANNOT-ASSESS is never a pass.**

``--check`` is the gate mode: it re-derives the view over the same in-process
graph and requires the two renders to be byte-identical (GR-12: a formula whose
ties depend on dict/set iteration order is a second source of truth wearing a
view's name), and additionally reconciles against ``--against FILE`` when given.

``priority`` and ``dispatch`` take the same ``--check``/``--json``/``--against``
contract as the other six views. ``dispatch`` additionally takes ``--wave``
(default: ``policy.yaml``'s ``wave_cap_default``) and never writes anything
unless ``--apply`` is passed (never used by the gate): ``--apply`` posts a
single idempotent PMO comment (``<!-- pmo-dispatch:v1 -->`` marker,
update-in-place) and the ``pmo:dispatched`` label per assigned issue, via the
``gh`` CLI, and is a no-op without network access. ``--live`` optionally
overlays freshly pulled GitHub labels on top of the committed board snapshot
(read-only ``gh issue list --json``); it degrades to the offline snapshot on
any failure (no ``gh``, no network, rate limit) rather than raising.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import clusters as clusters_mod
from dispatch import dispatch as derive_dispatch
from graph import CannotAssess, Graph, load
from policy import Policy
from policy import load as load_policy
from priority import priority as derive_priority
from views import VIEWS, Finding, View, reconcile

REPO_SLUG = "kushin77/agent-orchestrator"

#: the two derived-but-policy-dependent views, kept out of VIEWS because they
#: need `policy` as well as `graph` — everything else about their CLI contract
#: (tri-state exit, --check, --against, --json) is identical.
POLICY_VIEWS = ("priority", "dispatch")
ALL_VIEWS = sorted(set(VIEWS) | set(POLICY_VIEWS))


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


def _overlay_live_labels(graph: Graph) -> list[str]:
    """Best-effort: overlay freshly pulled GitHub labels (``--live``).

    Read-only (``gh issue list --json``), REST fallback when the GraphQL-backed
    default is rate-limited. Any failure — no ``gh`` binary, no network, an
    auth error, a rate limit REST also hits — degrades to the committed board
    snapshot already loaded: ``--live`` is additive input, never a reason to
    fail a view that otherwise runs fine offline. Returns the notes to render.
    """
    notes: list[str] = []
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", REPO_SLUG, "--state", "all",
             "--limit", "1000", "--json", "number,labels"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "gh issue list failed")
        payload = json.loads(result.stdout)
    except Exception as exc:  # noqa: BLE001 - --live degrades, never raises
        notes.append(f"--live: falling back to the offline board snapshot ({exc})")
        return notes

    updated = 0
    for row in payload if isinstance(payload, list) else []:
        number = row.get("number")
        labels = row.get("labels") or []
        if not isinstance(number, int):
            continue
        names = tuple(sorted(lbl.get("name", "") for lbl in labels if isinstance(lbl, dict)))
        if names and names != graph.labels.get(number, ()):
            graph.labels[number] = names
            updated += 1
    notes.append(f"--live: overlaid labels for {len(payload) if isinstance(payload, list) else 0} issue(s), {updated} changed")
    return notes


def _derive_view(args: argparse.Namespace, graph: Graph, policy: Policy, clusters=None) -> View:
    if args.view == "priority":
        return derive_priority(graph, policy)
    if args.view == "dispatch":
        return derive_dispatch(
            graph, policy, wave=args.wave or 1, wave_cap=args.wave_cap,
            by_cluster=args.by_cluster, clusters=clusters,
        )
    return VIEWS[args.view](graph)


def _apply_dispatch(view: View) -> list[str]:
    """``--apply``: post one idempotent PMO comment + label per assignment.

    Never used by the gate and never called unless ``--apply`` is on the
    command line. A failure to reach GitHub is reported per-issue and does not
    abort the remaining assignments — a plan is still useful partially applied.
    """
    MARKER = "<!-- pmo-dispatch:v1 -->"
    notes: list[str] = []
    for entry in view.document.get("assignments", []):
        ticket = entry["ticket"]
        number = ticket.rsplit("#", 1)[-1]
        body = (
            f"{MARKER}\n**PMO dispatch** — wave {view.document['wave']}, rank {entry['rank']}, "
            f"score {entry['score']}\n\nLane: `{entry['lane']}` · SME: `{entry['sme_profile']}` · "
            f"Tier: `{entry['tier']}` (`{entry['model']}`)\n\n```\n{entry['agent_brief']}\n```\n"
        )
        try:
            existing = subprocess.run(
                ["gh", "issue", "view", number, "--repo", REPO_SLUG, "--json", "comments"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            comment_id = None
            if existing.returncode == 0:
                payload = json.loads(existing.stdout)
                for comment in payload.get("comments", []):
                    if MARKER in (comment.get("body") or ""):
                        comment_id = comment.get("id")
                        break
            if comment_id:
                subprocess.run(
                    ["gh", "api", f"repos/{REPO_SLUG}/issues/comments/{comment_id}",
                     "-X", "PATCH", "-f", f"body={body}"],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                notes.append(f"apply: updated PMO comment on #{number}")
            else:
                subprocess.run(
                    ["gh", "issue", "comment", number, "--repo", REPO_SLUG, "--body", body],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                notes.append(f"apply: posted PMO comment on #{number}")
            subprocess.run(
                ["gh", "issue", "edit", number, "--repo", REPO_SLUG, "--add-label", "pmo:dispatched"],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except Exception as exc:  # noqa: BLE001 - one issue's failure isn't fatal
            notes.append(f"apply: #{number} failed ({exc})")
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pmo",
        description="PMO views derived from the ticket graph (issue #403 + follow-on).",
    )
    parser.add_argument("view", choices=ALL_VIEWS, help="the view to derive")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="emit the view as JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="gate mode: report findings as NOT-OK and reconcile --against a saved view",
    )
    parser.add_argument(
        "--against", default=None, metavar="FILE",
        help="a previously rendered view to reconcile this one against",
    )
    parser.add_argument(
        "--now", default=None, metavar="ISO",
        help="override the clock (default: the latest timestamp in the committed inputs)",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="priority/dispatch: overlay freshly pulled GitHub labels (read-only, degrades offline on failure)",
    )
    parser.add_argument(
        "--wave", type=int, default=None,
        help="dispatch: the wave number to record in the plan (default: 1)",
    )
    parser.add_argument(
        "--wave-cap", dest="wave_cap", type=int, default=None,
        help="dispatch: override policy.yaml's wave_cap_default",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="dispatch: compute the plan without --apply (this is also the default)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="dispatch: post one idempotent PMO comment + pmo:dispatched label per assignment (off by default; never used by the gate)",
    )
    parser.add_argument(
        "--by-cluster", dest="by_cluster", action="store_true",
        help="dispatch: dispatch one agent per batchable cluster from governance/pmo/clusters.json "
             "(optional, read-only input from a separate lane); falls back to the per-issue plan "
             "when the file is absent, and CANNOT-ASSESS (rc 2) when it is present but schema-invalid",
    )
    args = parser.parse_args(argv)

    try:
        graph: Graph = load(args.root)
        if args.now:
            graph.clock = args.now
        policy = load_policy(args.root) if args.view in POLICY_VIEWS else None
        clusters = clusters_mod.load(args.root) if args.view == "dispatch" and args.by_cluster else None
        live_notes: list[str] = []
        if args.live and args.view in POLICY_VIEWS:
            live_notes = _overlay_live_labels(graph)

        view = _derive_view(args, graph, policy, clusters)
        view.notes.extend(live_notes)
        findings = list(view.findings)
        if args.check:
            # A view is a projection, so deriving it twice over one graph must be
            # byte-identical: a render that depended on iteration order or the
            # wall clock would be a second source of truth wearing a view's name.
            again = _derive_view(args, graph, policy, clusters)
            if again.text() != view.text():
                findings.append(
                    Finding("view-nondeterministic", view.name, "two derivations over one graph differ")
                )
        if args.against:
            findings.extend(reconcile(view, _load_against(args.against)))

        if args.apply:
            if args.view != "dispatch":
                print("pmo: --apply is only meaningful for the dispatch view", file=sys.stderr)
                return 2
            view.notes.extend(_apply_dispatch(view))

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
