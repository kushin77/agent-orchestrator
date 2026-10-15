#!/usr/bin/env python3
"""CLI for the paperclip.ing adapter (issue #428).

Three verbs, matching the three things an operator needs from the seam:

* ``plan``  — build and print the offline dry-run sync plan (JSON or markdown).
  No network: it is a pure function of the tree.
* ``check`` — validate every mapped record against the three frozen seam
  schemas; tri-state exit (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS). This is what the
  gate calls.
* ``push``  — the live path: send the plan to the upstream ``/api`` surface over
  ``HttpTransport``. Implemented, and deliberately **never** run by the gate.

Run as ``python3 integrations/paperclip/cli.py <verb>`` from the repo root.

---knowledge---
module_id: integrations.paperclip.cli
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [cmd_plan, cmd_check, cmd_push, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations.paperclip import client as client_mod  # noqa: E402
from integrations.paperclip import mapping as mapping_mod  # noqa: E402
from integrations.paperclip.model import PaperclipError  # noqa: E402


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def cmd_plan(args: argparse.Namespace) -> int:
    root = _root(args)
    plan = mapping_mod.build_plan(root, company_id=args.company, session_id=args.session_id)
    if args.markdown:
        print(f"# paperclip-ing sync plan ({plan['paperclip']['company_id']})\n")
        print(f"mode: `{plan['paperclip']['mode']}` ({plan['paperclip']['adr']})\n")
        for section in ("agents", "tickets", "budgets", "heartbeats"):
            print(f"- {section}: {len(plan[section])}")
        print("\n## tickets")
        for ticket in plan["tickets"][: args.limit]:
            print(
                f"- `{ticket['id']}` -> {ticket['owner']} [{ticket['status']}] "
                f"goal={ticket['goal']}"
            )
        print("\n## budgets")
        for budget in plan["budgets"]:
            print(
                f"- {budget['scope']['level']}:{budget['scope']['id']} "
                f"cap={budget['cap']} {budget['currency']} spent={budget['spent']} "
                f"hard_stop={budget['hard_stop']}"
            )
    else:
        print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = _root(args)
    schema_dir = root / mapping_mod.SCHEMA_DIR
    if not schema_dir.is_dir():
        print(f"check: CANNOT-ASSESS — no seam schemas at {schema_dir}", file=sys.stderr)
        return 2
    schemas = mapping_mod.load_schemas(root)
    plan = mapping_mod.build_plan(root, company_id=args.company, session_id=args.session_id)
    findings = mapping_mod.validate_plan(plan, schemas)
    counts = {s: len(plan[s]) for s in mapping_mod.SECTION_SCHEMA}
    if findings:
        print(
            f"check: NOT-OK — {len(findings)} finding(s) across "
            f"{counts['heartbeats']} heartbeat(s), {counts['tickets']} ticket(s), "
            f"{counts['budgets']} budget(s)",
            file=sys.stderr,
        )
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        return 1
    print(
        "check: OK — "
        f"{counts['heartbeats']} heartbeat(s), {counts['tickets']} ticket(s), "
        f"{counts['budgets']} budget(s) conform to the three seam schemas"
    )
    return 0


def _existing_billing_codes(client: "client_mod.PaperclipClient") -> set:
    """Collect the ``billingCode`` of every issue already on the upstream board.

    The translator stamps ``billingCode`` with the seam ticket id, so this is the
    idempotency key: a scheduled re-run skips what already landed instead of
    duplicating the board (DRY; the sync is safe to put on a timer).
    """
    try:
        response = client.issues()
    except PaperclipError:
        return set()
    body = getattr(response, "body", None)
    if isinstance(body, dict):
        rows = body.get("issues") or body.get("items") or []
    elif isinstance(body, list):
        rows = body
    else:
        rows = []
    codes = set()
    for row in rows:
        if isinstance(row, dict) and row.get("billingCode"):
            codes.add(str(row["billingCode"]))
    return codes


def _load_ledger(path: "Path | None") -> set:
    """Read the pushed-ticket ledger (one seam ticket id per line)."""
    if path is None or not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _append_ledger(path: "Path | None", ids: list) -> None:
    """Append newly-pushed ticket ids to the ledger (create-if-missing)."""
    if path is None or not ids:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for ticket_id in ids:
            handle.write(f"{ticket_id}\n")


def _rows(response: object, *keys: str) -> list:
    """Read a list payload from a Response, tolerating an envelope or a bare list."""
    body = getattr(response, "body", None)
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in keys:
            rows = body.get(key)
            if isinstance(rows, list):
                return rows
    return []


def _agent_index(client: "client_mod.PaperclipClient") -> dict:
    """Map upstream agent id, name, and ``metadata.seam_agent_id`` -> upstream id.

    Resolving three ways is what makes the projection idempotent AND adoptable: a
    pre-existing agent (the bootstrap ``CEO``) is reused by name instead of
    duplicated, and a lost ledger still recovers assignees from the metadata tag.
    """
    index: dict = {}
    try:
        rows = _rows(client.agents(), "agents", "items")
    except PaperclipError:
        return index
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        agent_id = str(row["id"])
        index[agent_id] = agent_id
        name = row.get("name")
        if name:
            index[str(name)] = agent_id
            index[str(name).lower()] = agent_id
        meta = row.get("metadata") or {}
        seam = meta.get("seam_agent_id") if isinstance(meta, dict) else None
        if seam:
            index[str(seam)] = agent_id
    return index


def cmd_push(args: argparse.Namespace) -> int:
    root = _root(args)
    plan = mapping_mod.build_plan(root, company_id=args.company, session_id=args.session_id)
    # Token precedence: explicit --token, else $PAPERCLIP_TOKEN. The env path is
    # what the deployment wrapper uses: a secret on the command line is visible in
    # `ps`/`/proc/*/cmdline` to every local user, so the scheduled sync never
    # passes it as an argument.
    token = args.token or os.environ.get("PAPERCLIP_TOKEN", "")
    client = client_mod.PaperclipClient(
        args.base_url,
        token=token,
        company_id=args.company,
        run_id=args.run_id,
    )
    if args.dry_run:
        print(
            f"push(dry-run): would send {len(plan['tickets'])} ticket(s) to "
            f"{args.base_url}/api/companies/{args.company}/issues"
        )
        return 0
    # Idempotency has TWO sources on purpose. The upstream list endpoint is
    # page-capped (no offset), so on a board larger than one page it CANNOT be
    # enumerated -- a list-only dedupe silently re-sent 395 tickets and produced
    # 948 duplicate rows (measured 2026-10-15). The ledger is therefore
    # authoritative for "already pushed", and the board read is a best-effort
    # safety net for a lost/rebuilt ledger.
    ledger_path = Path(args.state_file).expanduser() if args.state_file else None
    agent_ledger_path = (
        Path(args.agent_state_file).expanduser() if args.agent_state_file else None
    )
    sent = 0
    skipped = 0
    agents_sent = 0
    agents_skipped = 0
    newly: list = []
    new_agents: list = []
    try:
        client.health()
        # 1) Project the ORG CHART first (idempotent, ledger-backed) so that step 2
        #    can carry a real assigneeAgentId — the upstream refuses in_progress
        #    without one, so order matters, not just tidiness.
        agent_ids = _agent_index(client)
        for agent in plan["agents"]:
            seam_id = str(agent["agent_id"])
            name = str(agent.get("name") or seam_id)
            resolved = agent_ids.get(seam_id) or agent_ids.get(name) or agent_ids.get(name.lower())
            if resolved:
                agent_ids[seam_id] = resolved
                agents_skipped += 1
                continue
            hired = client.create_agent_hire(mapping_mod.to_create_agent_body(agent))
            body = getattr(hired, "body", None) or {}
            approval = body.get("approval") or {}
            if args.approve_hires and approval.get("id"):
                client.approve(
                    str(approval["id"]),
                    "owner-authorized org projection (agent-orchestrator sync)",
                )
            agents_sent += 1
            new_agents.append(seam_id)
            created_id = str((body.get("agent") or {}).get("id") or "")
            if created_id:
                agent_ids[seam_id] = created_id
        _append_ledger(agent_ledger_path, new_agents)
        # 2) Tickets, owner resolved to an upstream agent id where we have one.
        existing = _existing_billing_codes(client) | _load_ledger(ledger_path)
        for ticket in plan["tickets"]:
            if ticket["id"] in existing:
                skipped += 1
                continue
            client.create_issue(
                mapping_mod.to_create_issue_body(
                    ticket, assignee_agent_id=agent_ids.get(str(ticket.get("owner", "")))
                )
            )
            sent += 1
            newly.append(ticket["id"])
    except PaperclipError as exc:
        _append_ledger(ledger_path, newly)
        _append_ledger(agent_ledger_path, new_agents)
        print(
            f"push: FAILED after {sent} ticket(s) / {agents_sent} agent(s) — {exc}",
            file=sys.stderr,
        )
        return 1
    _append_ledger(ledger_path, newly)
    print(
        f"push: agents sent {agents_sent} (skipped {agents_skipped}); "
        f"tickets sent {sent}, skipped {skipped}, "
        f"to {args.base_url}/api/companies/{args.company}"
    )
    return 0


# Statuses where an owner is actionable. Closed history (done/cancelled) is
# deliberately out of scope: a finished ticket needs no assignee, and enumerating
# 1000+ done rows would sit right on the page cap for no benefit.
ASSIGNABLE_STATUSES = ("todo", "blocked", "in_progress", "in_review", "backlog")


def cmd_assign(args: argparse.Namespace) -> int:
    """Backfill ``assigneeAgentId`` on open issues from the seam plan's owner.

    Tickets created before the org chart existed carry no assignee, because the
    seam owner is an agent NAME and an agent id did not exist yet. The projection
    creates the agents; this closes the loop so work in flight has an owner, by
    PATCHing the existing issues rather than re-sending them (which the ticket
    ledger would skip, and a re-send would duplicate).
    """
    plan = mapping_mod.build_plan(
        _root(args), company_id=args.company, session_id=args.session_id
    )
    owner_by_code = {
        str(ticket["id"]): str(ticket.get("owner", "")) for ticket in plan["tickets"]
    }
    # Same token precedence as push: explicit --token, else $PAPERCLIP_TOKEN, so a
    # secret never has to appear on the command line.
    token = args.token or os.environ.get("PAPERCLIP_TOKEN", "")
    client = client_mod.PaperclipClient(
        args.base_url,
        token=token,
        company_id=args.company,
        run_id=args.run_id,
    )
    assigned = 0
    skipped = 0
    try:
        client.health()
        agent_ids = _agent_index(client)
        for status in ASSIGNABLE_STATUSES:
            for issue in _rows(client.issues_by_status(status), "issues", "items"):
                if not isinstance(issue, dict) or not issue.get("id"):
                    continue
                code = str(issue.get("billingCode") or "")
                wanted = agent_ids.get(owner_by_code.get(code, ""))
                if not wanted or str(issue.get("assigneeAgentId") or "") == wanted:
                    skipped += 1
                    continue
                client.update_issue(str(issue["id"]), {"assigneeAgentId": wanted})
                assigned += 1
    except PaperclipError as exc:
        print(f"assign: FAILED after {assigned} — {exc}", file=sys.stderr)
        return 1
    print(f"assign: assigned {assigned}, skipped {skipped} (already owned / no mapping)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperclip-adapter",
        description="paperclip.ing integration adapter (issue #428, ADR-0013)",
    )
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    parser.add_argument("--company", default="purebliss", help="upstream company id")
    parser.add_argument("--session-id", default="unbound", help="minted session id")
    sub = parser.add_subparsers(dest="verb", required=True)

    plan = sub.add_parser("plan", help="print the offline dry-run sync plan")
    plan.add_argument("--markdown", action="store_true", help="markdown instead of JSON")
    plan.add_argument("--limit", type=int, default=20, help="tickets shown in markdown")
    plan.set_defaults(func=cmd_plan)

    check = sub.add_parser("check", help="validate the mapping against the seam schemas")
    check.set_defaults(func=cmd_check)

    push = sub.add_parser("push", help="send the plan to the upstream /api surface")
    push.add_argument("--base-url", required=True, help="upstream server root")
    push.add_argument("--token", default="", help="upstream agent key / JWT / board token (or $PAPERCLIP_TOKEN)")
    push.add_argument("--run-id", default=None, help="X-Paperclip-Run-Id for mutating calls (must reference an existing heartbeat run)")
    push.add_argument("--state-file", default="", help="pushed-ticket ledger; makes re-runs idempotent beyond the upstream page cap")
    push.add_argument("--agent-state-file", default="", help="pushed-agent ledger (seam agent ids); idempotent org-chart projection")
    push.add_argument(
        "--approve-hires",
        action="store_true",
        help="resolve the hire approval each projection creates (the company requires board approval for new agents)",
    )
    push.add_argument("--dry-run", action="store_true", help="print what would be sent")
    push.set_defaults(func=cmd_push)

    assign = sub.add_parser(
        "assign",
        help="backfill owners on open issues now that the org chart exists",
    )
    assign.add_argument("--base-url", required=True, help="upstream server root")
    assign.add_argument("--token", default="", help="upstream board token (or $PAPERCLIP_TOKEN)")
    assign.add_argument("--run-id", default=None, help="X-Paperclip-Run-Id for mutating calls")
    assign.set_defaults(func=cmd_assign)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
