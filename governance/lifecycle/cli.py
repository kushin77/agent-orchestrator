#!/usr/bin/env python3
"""Lifecycle command line — audit the board's hygiene, and close an item out (#269).

    python3 governance/lifecycle/cli.py audit            # live board, offline rules
    python3 governance/lifecycle/cli.py audit --record r.json --json
    python3 governance/lifecycle/cli.py close --issue 269
    python3 governance/lifecycle/cli.py status --issue 269

Two deliberate splits:

* **Collecting is not auditing.** ``collect`` reaches GitHub; ``audit`` never
  does. The rules are exercised offline against a record, so a gate can assert
  the mechanism against fixtures it mutates instead of depending on live state —
  the staleness trap measured in ``.board/snapshot.json`` (#170).
* **Durable evidence lives in the repo, not in the board.** What was verified and
  merged is journalled under ``.fleet/lifecycle/<issue>.json``, so the audit reads
  facts it can re-check rather than a snapshot that silently ages.

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.lifecycle.audit import audit, hygiene, load_quarantine  # noqa: E402
from governance.lifecycle.closeout import CloseOutResult, closeout, describe  # noqa: E402
from governance.lifecycle.model import STAGES, stage_of  # noqa: E402

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

JOURNAL_DIR = ".fleet/lifecycle"
BASELINE = Path("governance/lifecycle/baseline.json")

CLOSE_PATTERN = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
BRANCH_PATTERN = re.compile(r"^issue-(\d+)")


def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _gh(*args: str) -> list | dict:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()[-200:]}")
    return json.loads(result.stdout or "null")


def journal_path(issue: int, root: Path | None = None) -> Path:
    return (root or ROOT) / JOURNAL_DIR / f"{issue}.json"


def read_journal(issue: int, root: Path | None = None) -> dict:
    return _read_json(journal_path(issue, root))


def write_journal(issue: int, patch: dict, root: Path | None = None) -> Path:
    path = journal_path(issue, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_journal(issue, root)
    current.update(patch)
    path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _live_claims(root: Path | None = None) -> dict[int, str]:
    """Live claims from the claim ledger, keyed by issue number."""
    root = root or ROOT
    ledger = root / ".board" / "claims.jsonl"
    if not ledger.exists():
        return {}
    dispatch_dir = str(root / "governance" / "dispatch")
    if dispatch_dir not in sys.path:
        sys.path.insert(0, dispatch_dir)
    try:
        import claims as dispatch_claims  # noqa: PLC0415 - optional at import time
    except ImportError:
        return {}
    try:
        events = dispatch_claims.read_ledger(ledger)
        return {number: claim.agent for number, claim in dispatch_claims.active_claims(events).items()}
    except Exception:  # noqa: BLE001 - an unreadable ledger is absence of evidence, not a crash
        return {}


def _lane_records(root: Path | None = None) -> dict[int, dict]:
    """Provisioned lanes keyed by issue, from the isolation module's records."""
    root = root or ROOT
    directory = root / ".fleet" / "lanes"
    lanes: dict[int, dict] = {}
    if not directory.exists():
        return lanes
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        worktree = Path(str(payload.get("worktree", "")))
        lanes[int(payload["issue"])] = {
            "session_id": str(payload.get("session_id", "")),
            "worktree": str(worktree),
            "present": worktree.exists(),
        }
    return lanes


def _directive_for(issue: int, root: Path | None = None) -> dict:
    """The authorisation directive that dispatched an item, and whether it is consumed."""
    root = root or ROOT
    for path in sorted((root / ".fleet" / "sent").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if int((payload.get("task") or {}).get("issue") or 0) != issue:
            continue
        consumed = (root / ".fleet" / "done" / path.name).exists()
        return {"id": str(payload.get("id", path.stem)), "state": "done" if consumed else "sent"}
    return {}


def _branch_exists(branch: str) -> bool:
    if not branch:
        return False
    return bool(_git("ls-remote", "--heads", "origin", branch).strip())


def collect_from_github(root: Path | None = None) -> dict:
    """The live lifecycle record: fleet-touched items plus open milestoned items."""
    root = root or ROOT
    issues = _gh("issue", "list", "--state", "all", "--limit", "500",
                 "--json", "number,state,title,labels,milestone")
    pulls = _gh("pr", "list", "--state", "all", "--limit", "500",
                "--json", "number,state,headRefName,headRefOid,mergeCommit,body,title")

    by_issue: dict[int, dict] = {}
    for pull in pulls:
        numbers = {int(n) for n in CLOSE_PATTERN.findall(pull.get("body") or "")}
        match = BRANCH_PATTERN.match(str(pull.get("headRefName") or ""))
        if match:
            numbers.add(int(match.group(1)))
        for number in numbers:
            by_issue.setdefault(number, pull)

    lanes = _lane_records(root)
    claims = _live_claims(root)
    journals = {}
    journal_dir = root / JOURNAL_DIR
    if journal_dir.exists():
        for path in journal_dir.glob("*.json"):
            try:
                journals[int(path.stem)] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

    items = []
    for issue in issues:
        number = int(issue["number"])
        lane = lanes.get(number)
        pull = by_issue.get(number)
        claimed_by = claims.get(number)
        closed = issue.get("state") == "closed"
        # Scope: work the fleet touched, plus open milestoned work (the filing rule).
        if not (lane or pull or claimed_by or (not closed and issue.get("milestone"))):
            continue
        journal = journals.get(number) or {}
        items.append(
            {
                "issue": number,
                "title": issue.get("title", ""),
                "state": issue.get("state", "open"),
                "milestone": (issue.get("milestone") or {}).get("title"),
                "labels": [label["name"] for label in issue.get("labels") or []],
                "pr": (
                    {
                        "number": int(pull["number"]),
                        "state": pull.get("state"),
                        "branch": pull.get("headRefName", ""),
                        "head_commit": pull.get("headRefOid", ""),
                        "merge_commit": (pull.get("mergeCommit") or {}).get("oid", ""),
                    }
                    if pull
                    else {}
                ),
                "branch_deleted": not _branch_exists(str((pull or {}).get("headRefName") or "")),
                "claim": {"agent": claimed_by, "live": bool(claimed_by)},
                "directive": _directive_for(number, root),
                "lane": lane or {},
                "verify": journal.get("verify") or {},
                "closing_evidence": bool(journal.get("closing_evidence", False)),
            }
        )

    tracking = {}
    baseline = load_baseline(root)
    wanted = {entry["tracked_by"] for entry in baseline.get("quarantine", [])}
    for reference in wanted:
        state = next((i["state"] for i in issues if f"#{i['number']}" == reference), "unknown")
        tracking[reference] = state

    return {
        "scope": "fleet-touched items, plus open milestoned items (filing rule)",
        "collected_at": _git("log", "-1", "--format=%cI").strip(),
        "items": items,
        "tracking": tracking,
    }


def _read_json(path: Path) -> dict:
    """Read a JSON document, treating an absent or unreadable file as empty."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_baseline(root: Path | None = None) -> dict:
    """The repository's declared legacy quarantine."""
    return _read_json((root or ROOT) / BASELINE) or {"quarantine": []}


def read_baseline(path: str) -> dict:
    """Read a baseline document; an explicit path overrides the repo default.

    Fixtures and lanes need to audit against their own declaration rather than
    the repository's, or the repo's quarantine would silently excuse items a test
    is trying to provoke.
    """
    if not path:
        return load_baseline()
    return _read_json(Path(path)) or {"quarantine": []}


class GhOps:
    """The real effects close-out needs, through ``gh`` and the repo's own CLIs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or ROOT

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        result = subprocess.run(args, cwd=str(cwd or self.root), capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-200:] or "command failed")
        return (result.stdout or "").strip()

    def merge_pull_request(self, number: int) -> str:
        # The local delete-branch step fails while the main checkout holds master;
        # the merge itself lands, and step 3 deletes the branch explicitly.
        try:
            self._run(["gh", "pr", "merge", str(number), "--squash"])
        except RuntimeError:
            pass  # Already merged is not a failure here; the audit re-derives the truth.
        view = self._run(["gh", "pr", "view", str(number), "--json", "state,mergeCommit"])
        payload = json.loads(view)
        if payload.get("state") != "MERGED":
            raise RuntimeError(f"PR {number} is {payload.get('state')}, not merged")
        return str((payload.get("mergeCommit") or {}).get("oid", ""))

    def record_verification(self, issue: int, commit: str) -> str:
        """Re-run the repo gate in the lane and journal the attestation it writes."""
        lane = _lane_records(self.root).get(issue)
        if not lane or not lane.get("present"):
            raise RuntimeError(f"no lane worktree for #{issue}; the verified tree no longer exists")
        worktree = Path(lane["worktree"])
        head = self._run(["git", "-C", str(worktree), "rev-parse", "HEAD"])
        if commit and head != commit:
            raise RuntimeError(f"lane head {head[:12]} is not the verified commit {commit[:12]}")
        self._run(["make", "verify"], cwd=worktree)
        write_journal(issue, {"verify": {"ok": True, "commit": head}}, self.root)
        return f"verify green at {head[:12]}"

    def delete_branch(self, branch: str) -> str:
        self._run(["git", "-C", str(self.root), "push", "origin", "--delete", branch])
        return f"deleted origin/{branch}"

    def consume_directive(self, directive_id: str) -> str:
        return self._run(["python3", str(self.root / "fleet" / "channel.py"), "consume", "--id", directive_id])

    def release_claim(self, issue: int, agent: str) -> str:
        return self._run(
            ["python3", str(self.root / "governance" / "dispatch" / "cli.py"), "release",
             "--issue", str(issue), "--agent", agent]
        )

    def record_closing_evidence(self, issue: int, evidence: str) -> str:
        """Journal the evidence that justifies closing the item."""
        write_journal(issue, {"closing_evidence": True, "evidence": evidence}, self.root)
        return "closing evidence journalled"

    def close_issue(self, issue: int, evidence: str) -> str:
        return self._run(["gh", "issue", "close", str(issue), "--comment", evidence])

    def reclaim_lane(self, session_id: str) -> str:
        return self._run(
            ["python3", str(self.root / "governance" / "isolation" / "cli.py"), "close", "--session", session_id]
        )


def cmd_audit(args: argparse.Namespace) -> int:
    record = json.loads(Path(args.record).read_text(encoding="utf-8")) if args.record else collect_from_github()
    quarantine = load_quarantine(read_baseline(args.baseline))
    report = hygiene(record, quarantine)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"scope: {report['scope']}")
        print(f"items: {report['items']} ({report['closed']} closed)")
        for finding in report["findings"]:
            print(f"  {finding['code']:<26} {finding['subject']:<8} {finding['detail']}")
            print(f"    -> {finding['remediation']}")
    if report["hygienic"]:
        print(f"lifecycle-hygiene: OK ({report['items']} item(s), 0 finding(s))")
        return EXIT_OK
    print(f"lifecycle-hygiene: FAIL ({len(report['findings'])} finding(s))", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_status(args: argparse.Namespace) -> int:
    record = collect_from_github()
    item = next((entry for entry in record["items"] if entry["issue"] == args.issue), None)
    if item is None:
        print(f"status: CANNOT-ASSESS — #{args.issue} is outside the audit scope", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    print(json.dumps({**item, "stage": stage_of(item), "stages": list(STAGES)}, indent=2))
    return EXIT_OK


def cmd_close(args: argparse.Namespace) -> int:
    record = collect_from_github()
    item = next((entry for entry in record["items"] if entry["issue"] == args.issue), None)
    if item is None:
        print(f"close: CANNOT-ASSESS — #{args.issue} is outside the audit scope", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    result: CloseOutResult = closeout(item, GhOps(), evidence=args.evidence)
    print(describe(result))
    return EXIT_OK if result.ok else EXIT_NOT_OK


def cmd_collect(args: argparse.Namespace) -> int:
    record = collect_from_github()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"collect: wrote {out} ({len(record['items'])} item(s))")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lifecycle", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    audit_cmd = sub.add_parser("audit", help="report every item that did not close hygienically")
    audit_cmd.add_argument("--record", default="", help="read a recorded lifecycle document instead of collecting live")
    audit_cmd.add_argument("--baseline", default="", help="the legacy quarantine to honour (default: the repo's)")
    audit_cmd.add_argument("--json", action="store_true")
    audit_cmd.set_defaults(func=cmd_audit)

    status_cmd = sub.add_parser("status", help="where one item sits, and what it still owes")
    status_cmd.add_argument("--issue", type=int, required=True)
    status_cmd.set_defaults(func=cmd_status)

    close_cmd = sub.add_parser("close", help="drive one item to hygiene, in dependency order")
    close_cmd.add_argument("--issue", type=int, required=True)
    close_cmd.add_argument("--evidence", default="", help="the evidence to record when closing")
    close_cmd.set_defaults(func=cmd_close)

    collect_cmd = sub.add_parser("collect", help="write the live lifecycle record")
    collect_cmd.add_argument("--out", required=True)
    collect_cmd.set_defaults(func=cmd_collect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"{args.command}: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
