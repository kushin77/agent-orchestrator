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

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. The third value is load
bearing: a close-out whose verification could not be *assessed* (the gate was
parked at its box-wide cap, its permit store was unusable, or it was killed by a
signal) is rc 2 and never rc 0 or rc 1 — nothing was measured, so neither a pass
nor a failure may be reported (#840).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.lifecycle import directive, gate  # noqa: E402
from governance.lifecycle.audit import audit, hygiene, in_scope, load_quarantine  # noqa: E402
from governance.lifecycle.closeout import CloseOutResult, closeout, describe  # noqa: E402
from governance.lifecycle.model import STAGES, stage_of  # noqa: E402
from governance.lifecycle.report import (  # noqa: E402
    DEDUPED,
    FILED,
    BoardReporter,
    GhFiler,
    board_report_findings,
)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

JOURNAL_DIR = ".fleet/lifecycle"
BASELINE = Path("governance/lifecycle/baseline.json")

#: Where a verification is re-measured once the lane is gone: a throwaway worktree
#: that cannot outlive the run. Unlike a lane — which ``governance/isolation``
#: refuses from a RAM-backed root because a lane outlives a reboot — this one is
#: scratch by construction, so tmpfs is fine.
SCRATCH_ENV = "AO_LIFECYCLE_SCRATCH"


def scratch_root() -> Path:
    """The directory a re-measurement worktree is created under."""
    override = os.environ.get(SCRATCH_ENV, "").strip()
    return Path(override) if override else Path(tempfile.gettempdir())


CLOSE_PATTERN = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
BRANCH_PATTERN = re.compile(r"^issue-(\d+)")


def _reporter() -> BoardReporter:
    """The board-reporting seam for this repo (issue #321), dry-run gated by
    each command's own ``--apply`` flag."""
    return BoardReporter(GhFiler(), ledger=ROOT / ".fleet" / "board-reports.json")


def _print_board_reports(reports: list) -> None:
    for board_report in reports:
        if board_report.action == FILED:
            print(f"board: filed #{board_report.number} for {board_report.key}")
        elif board_report.action == DEDUPED:
            print(f"board: already filed (#{board_report.number}) for {board_report.key}")
        else:
            print(f"board: dry-run — would file for {board_report.key}")


def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _gate_attempt(worktree: Path) -> gate.GateAttempt:
    """Run the repo gate once in ``worktree`` and report what it said, unraised.

    ``subprocess`` directly rather than the ``_run`` helper, because the whole point
    is that a non-zero exit is *data*: an admission refusal means the gate never
    started, and raising every non-zero code into one generic handler is what made a
    parked run read as a failed verification (#840).

    ``stderr`` is folded into ``stdout`` **in the child** so the transcript keeps its
    real order: the gate prints a failing run's verdict to stderr and each check's
    output through ``tee`` to stdout, so appending one stream to the other afterwards
    would put the verdict before the lines it follows — and the verdict is read from
    the end.
    """
    result = subprocess.run(
        ["make", "verify"], cwd=str(worktree), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return gate.GateAttempt(exit_code=result.returncode, output=result.stdout or "")


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


def _lane_records(root: Path | None = None) -> dict[int, list[dict]]:
    """**Every** provisioned lane record, keyed by issue (#834).

    A list, not one record per issue. ``.fleet/lanes/`` can hold more than one
    record for an issue — the lane that provisioned a worktree, and a sibling
    whose worktree has since been removed — and collapsing them by issue let the
    later-sorted record win, so a *dead* record shadowed the live lane. Measured
    on #287: ``record-verification`` refused ``no lane worktree for #287; the
    verified tree no longer exists`` while the live lane existed, audited clean,
    and held the verified commit. The choice is now explicit (:func:`select_lane`)
    instead of accidental.
    """
    root = root or ROOT
    directory = root / ".fleet" / "lanes"
    lanes: dict[int, list[dict]] = {}
    if not directory.exists():
        return lanes
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        worktree = Path(str(payload.get("worktree", "")))
        lanes.setdefault(int(payload["issue"]), []).append(
            {
                "session_id": str(payload.get("session_id", "")),
                "worktree": str(worktree),
                "worktree_exists": worktree.exists(),
            }
        )
    return lanes


def lane_head(record: dict) -> str:
    """The commit a lane's worktree holds, or ``""`` when there is no tree to read."""
    if not record.get("worktree_exists"):
        return ""
    result = subprocess.run(
        ["git", "-C", str(record.get("worktree") or ""), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def select_lane(records: list[dict] | None, commit: str = "") -> dict | None:
    """The record that represents the item's lane, out of every record it has.

    Two preferences, in order (#834):

    1. **a record whose worktree exists** wins over one whose worktree is gone —
       a dead record may never shadow a live lane, which is the wedge that made
       ``record-verification`` refuse a lane that was there all along;
    2. among equally live records, **the one whose HEAD is ``commit``** when the
       caller knows the verified commit, because that is the tree the evidence is
       about. Reading a HEAD costs a subprocess, so it is only paid when a commit
       was named and there is more than one live candidate to choose between.

    A dead record is still *returned* when it is all there is: the caller must be
    able to say *worktree-missing* rather than "no lane worktree", which is what
    tells an operator the tree was removed rather than never created.
    """
    candidates = list(records or [])
    if not candidates:
        return None
    live = [record for record in candidates if record.get("worktree_exists")]
    pool = live or candidates
    if commit and len(pool) > 1:
        for record in pool:
            if lane_head(record) == commit:
                return record
    return pool[0]


def lane_view(records: list[dict] | None, commit: str = "") -> dict:
    """The item's lane, as the audit and the close-out both read it (#834).

    ``present`` means the lane is **not reclaimed — a session record remains**,
    which is exactly what the closure invariant demands be gone, and therefore
    what makes close-out step 8 run. ``worktree_exists`` says whether the tree is
    still there: a record without one is a *dead* lane record whose worktree a
    reaper already removed, and it is reported by name (:func:`audit_item`) rather
    than treated as nothing — treating it as nothing is how it came to shadow a
    live lane in the first place.
    """
    chosen = select_lane(records, commit)
    if chosen is None:
        return {}
    every = list(records or [])
    return {
        "session_id": chosen["session_id"],
        "worktree": chosen["worktree"],
        "present": True,
        "worktree_exists": bool(chosen["worktree_exists"]),
        "sessions": [record["session_id"] for record in every],
        "dead": [record["session_id"] for record in every if not record["worktree_exists"]],
    }


def _directive_for(issue: int, root: Path | None = None) -> dict:
    """The authorisation directive that dispatched an item, and whether it is consumed.

    A **stranded** directive wins over a terminal one for the same issue (#821).
    The old reader returned the first match in filename order and asked only
    whether a ``done/`` file happened to share its name, so a lane whose *older*
    authorisation had already reached ``done`` read as fully consumed while the
    directive that actually dispatched it stayed in ``.fleet/sent/`` forever —
    which is why this failure looked intermittent across lanes and was not.
    ``directive.records`` lists sent before done, so the pending record is the one
    the invariant is decided on.
    """
    root = root or ROOT
    for record in directive.records(root):
        if record.issue == issue:
            return {"id": record.id, "state": record.state}
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
        lane = lane_view(lanes.get(number))
        pull = by_issue.get(number)
        claimed_by = claims.get(number)
        closed = issue.get("state") == "closed"
        directive = _directive_for(number, root)
        journal = journals.get(number) or {}
        # Scope: items the process owns (lane/claim/directive/journal), plus open
        # milestoned work for the filing rule. A PR alone is NOT ownership - a
        # historical item closed before the lifecycle existed has one.
        if not in_scope(
            lane=lane,
            claim=claimed_by,
            directive=directive,
            journal=journal,
            closed=closed,
            milestone=(issue.get("milestone") or {}).get("title"),
        ):
            continue
        items.append(
            {
                "issue": number,
                "title": issue.get("title", ""),
                "state": str(issue.get("state", "open")).lower(),
                "milestone": (issue.get("milestone") or {}).get("title"),
                "labels": [label["name"] for label in issue.get("labels") or []],
                "pr": (
                    {
                        "number": int(pull["number"]),
                        "state": str(pull.get("state", "")).lower(),
                        "branch": pull.get("headRefName", ""),
                        "head_commit": pull.get("headRefOid", ""),
                        "merge_commit": (pull.get("mergeCommit") or {}).get("oid", ""),
                    }
                    if pull
                    else {}
                ),
                "branch_deleted": not _branch_exists(str((pull or {}).get("headRefName") or "")),
                "claim": {"agent": claimed_by, "live": bool(claimed_by)},
                "directive": directive,
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
        tracking[reference] = str(state).lower()

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

    def __init__(self, root: Path | None = None, record: dict | None = None) -> None:
        self.root = root or ROOT
        #: The lifecycle record the close-out was collected from. The directive's
        #: terminal move is gated on whether the order's change has landed, and
        #: that fact comes from the board read that already happened — not from a
        #: second, possibly different, read of the world (#821).
        self.record = record

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

    def _gate_in(self, worktree: Path, described: str) -> None:
        """Run the repo gate in ``worktree`` and read its verdict through the closed table.

        The lane is *resolved*, never guessed (#834): a record whose worktree is
        still there wins over a dead sibling, and among live records the one whose
        HEAD is the verified commit wins. A lane whose only record has no worktree
        is refused naming ``worktree-missing`` — so the operator learns the tree is
        gone, not that no lane ever existed (the message that sent #287's lane
        hunting for a worktree it had provisioned itself) — and a lane that is gone
        while its verified commit is still in the object store is **re-measured at
        that commit** rather than mourned (#786), so the invariant stays satisfiable.

        The exit code is read through ``governance.lifecycle.gate``, whose closed
        table is the only place that decision lives: a park (10/11), an unusable
        permit store (12), the gate's own CANNOT-ASSESS (2) and a signal are all
        *absences of a result*, and are raised as ``gate.CannotAssess`` so the
        close-out reports CANNOT-ASSESS instead of filing a missing verification
        nobody measured (#840). A genuine failure (rc 1) is still a failure, and
        still leaves the item's evidence missing.

        One reader for both trees — the lane's and the re-measurement's — so the two
        paths cannot drift into reading the same gate differently.
        """
        run = gate.run_gate(lambda: _gate_attempt(worktree))
        if run.admitted:
            return
        if run.cannot_assess:
            raise gate.CannotAssess(run.verdict, run.detail(), run.remediation())
        raise RuntimeError(
            f"{run.detail()} — the gate ran against {described} and reported a failure, "
            "so the item has no green verification"
        )

    def record_verification(self, issue: int, commit: str) -> str:
        """Record a green attestation for ``commit``, from the lane or from the commit.

        The lane is the first source: the gate is re-run in it, and the attestation it
        writes is journalled. That is the strongest evidence available, because the
        tree and the run are the same object.

        When the lane is gone the invariant is **re-measured from the commit**, not
        declared missing (#786). ``record-verification`` used to require the lane
        worktree, which made the invariant *permanently unsatisfiable* the instant a
        lane was reclaimed — measured on #622/#623/#626, where the evidence of three
        green trees was discarded because it had not been journalled before teardown
        and every later pass refused with "the verified tree no longer exists". The
        invariant names the **verified head commit**, and the commit — not the branch,
        not the worktree — is what proves it; a commit that is still in the object
        store can still be measured.

        A run that did not happen writes **no journal**. ``.fleet/lifecycle``'s
        presence is another module's landing record — ``governance/reconcile``
        reads a journal file as "this issue's work landed" — so writing one for a
        parked run would let capacity be read as a landing, the substitution
        golden rule 17 forbids. The attempts are recorded where close-out prints
        them instead; nothing carries them as evidence.
        """
        records = _lane_records(self.root).get(issue) or []
        lane = select_lane(records, commit)
        if lane is not None and lane["worktree_exists"]:
            worktree = Path(lane["worktree"])
            head = self._run(["git", "-C", str(worktree), "rev-parse", "HEAD"])
            if commit and head != commit:
                raise RuntimeError(f"lane head {head[:12]} is not the verified commit {commit[:12]}")
            self._gate_in(worktree, head[:12])
            write_journal(issue, {"verify": {"ok": True, "commit": head, "source": "lane"}}, self.root)
            return f"verify green at {head[:12]}"
        if any(record["worktree_exists"] for record in records):
            # A live lane exists yet the resolution did not return it (#834). The gate
            # must run IN the lane — that is the strongest evidence there is, and the
            # reason a dead record may never shadow it — so a broken choice is refused
            # by name rather than papered over by re-measuring somewhere else.
            raise RuntimeError(
                f"lane {(lane or {}).get('session_id') or '(unknown)'} for #{issue} is worktree-missing: "
                f"{(lane or {}).get('worktree') or '(unknown)'} does not exist, but another record's "
                "worktree does — the resolution picked a dead record over a live lane"
            )
        # The lane is gone: a dead record (#834 keeps it whole and names it) or no
        # record at all. The invariant names the COMMIT (#786), so a commit that is
        # still in the object store is re-measured rather than mourned — and when
        # there is none, ``_remeasure`` refuses by name, naming the ordering.
        measured = self._remeasure(issue, commit, dead=lane)
        write_journal(
            issue,
            {"verify": {"ok": True, "commit": measured, "source": "reclaimed-lane"}},
            self.root,
        )
        return f"verify green at {measured[:12]} (re-measured at the verified commit; the lane is gone)"

    def _remeasure(self, issue: int, commit: str, dead: dict | None = None) -> str:
        """Re-measure a verified commit whose lane worktree no longer exists.

        ``dead`` is the lane record the resolution found whose worktree is gone, when
        one exists (#834). It is carried only so the refusal below can keep naming
        ``worktree-missing`` — the isolation audit's own vocabulary — instead of the
        old "no lane worktree", which sent the operator hunting for a tree the lane
        had provisioned itself.

        The commit is checked out into a **throwaway detached worktree** and the gate
        is run there — a real measurement at the real commit, never an inherited
        claim, and the tree is destroyed again either way so a re-measurement cannot
        itself become a lane.

        A commit that is not in this repository is refused **by name**, and the
        refusal names the ordering, because that is the fact an operator can act on:
        the lane was reclaimed before close-out recorded its verification. That
        ordering is no longer merely advised — ``governance/lifecycle/closeout.py``
        refuses to reclaim a lane while the item still owes this record, so a new
        instance of this state cannot be created by the driver.
        """
        if not commit:
            raise RuntimeError(
                f"no lane worktree for #{issue} and no verified head commit is recorded, so there is "
                "nothing to re-measure: the lane was reclaimed before close-out journalled its "
                "verification. The ordering is close-out BEFORE lane teardown"
            )
        probe = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--verify", f"{commit}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            if dead is not None:
                raise RuntimeError(
                    f"lane {dead['session_id'] or '(unknown)'} for #{issue} is worktree-missing: "
                    f"{dead['worktree'] or '(unknown)'} does not exist, and the verified commit "
                    f"{commit[:12]} is not in this repository either, so its attestation cannot be "
                    "re-measured. The ordering is close-out BEFORE lane teardown: keep the lane (or "
                    "the commit) until `governance/lifecycle/cli.py close` has journalled the "
                    f"attestation (looked in {self.root})"
                )
            raise RuntimeError(
                f"no lane worktree for #{issue}: the verified commit {commit[:12]} is not in this "
                "repository, so its attestation cannot be re-measured — the lane was reclaimed before "
                "close-out recorded its verification. The ordering is close-out BEFORE lane teardown: "
                "keep the lane (or the commit) until `governance/lifecycle/cli.py close` has journalled "
                f"the attestation (looked in {self.root})"
            )
        resolved = probe.stdout.strip()
        root_dir = scratch_root()
        root_dir.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="ao-lifecycle-verify-", dir=str(root_dir)))
        try:
            self._run(["git", "-C", str(self.root), "worktree", "add", "--detach", str(scratch), resolved])
            self._gate_in(scratch, resolved[:12])
        finally:
            # Always give the tree back: a leaked worktree is another lane's
            # isolation problem, and this one is scratch by construction.
            cleanup = subprocess.run(
                ["git", "-C", str(self.root), "worktree", "remove", "--force", str(scratch)],
                capture_output=True,
                text=True,
            )
            if cleanup.returncode != 0:
                subprocess.run(
                    ["git", "-C", str(self.root), "worktree", "prune"], capture_output=True, text=True
                )
        return resolved

    def delete_branch(self, branch: str) -> str:
        self._run(["git", "-C", str(self.root), "push", "origin", "--delete", branch])
        return f"deleted origin/{branch}"

    def consume_directive(self, directive_id: str) -> str:
        """Retire the order: the terminal move is the close-out's own (#821).

        This used to shell out to ``fleet/channel.py consume``, which reads only
        the *inbox* — a mailbox a brain-minted directive never enters — so the step
        could not reach its terminal state for the artifact it was given, and the
        remedy the finding named was unreachable by construction. Whether an order
        is finished is a fact the lifecycle already holds; the move therefore lives
        in ``governance/lifecycle/directive.py``, refused by name when the ordered
        change has not landed so it can never retire work still in flight.
        """
        record = self.record if self.record is not None else collect_from_github(self.root)
        return directive.consume(self.root, directive_id, directive.landed_issues(record))

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
        """Reclaim the item's lane, and the dead records beside it (#834).

        An issue can carry more than one lane record: the live one, and a sibling
        whose worktree a reaper already removed. The invariant is about the
        *item's records* being gone, so reclaiming only the live one would leave
        the sibling to be reported by the very next audit — and the item could
        then never reach OK. A sibling whose worktree is gone holds no work, so
        retiring it discards nothing; a sibling whose tree still exists is left
        alone, because that tree is a lane with its own close-out.
        """
        # Imported here rather than at module scope: this file is copied into a
        # scratch repository by scripts/check-control-verbs.sh, where
        # governance/isolation need not exist — the same reason ``_live_claims``
        # imports its claims module lazily.
        from governance.isolation.worktree import list_records, read_record  # noqa: PLC0415

        targets = [session_id]
        identity = read_record(session_id, self.root)
        if identity is not None:
            targets += [
                other.session_id
                for other in list_records(self.root)
                if other.issue == identity.issue and other.session_id != session_id and not other.worktree.exists()
            ]
        for target in targets:
            self._run(
                ["python3", str(self.root / "governance" / "isolation" / "cli.py"), "close", "--session", target]
            )
        return f"reclaimed {len(targets)} session record(s): {', '.join(targets)}"

    def refresh(self, item: dict) -> dict:
        """Re-collect this item from the live board, so the final audit reads the
        world the close-out just changed rather than the stale pre-close state."""
        issue = int(item.get("issue") or 0)
        for fresh in collect_from_github(self.root)["items"]:
            if fresh["issue"] == issue:
                return fresh
        return item


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
    findings = audit(record, quarantine)
    board_reports = board_report_findings(findings, _reporter(), apply=args.apply)
    if not args.json:
        _print_board_reports(board_reports)
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
    """Drive an item to hygiene; the verdict decides the exit code.

    Three outcomes, three codes: OK (0), NOT-OK (1) when an invariant is measured
    broken, and CANNOT-ASSESS (2) when nothing is known broken but something could
    not be measured — a parked verification is the case this exists for (#840).
    """
    record = collect_from_github()
    item = next((entry for entry in record["items"] if entry["issue"] == args.issue), None)
    if item is None:
        print(f"close: CANNOT-ASSESS — #{args.issue} is outside the audit scope", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    result: CloseOutResult = closeout(
        item, GhOps(record=record), evidence=args.evidence, reporter=_reporter(), apply=args.apply
    )
    print(describe(result))
    _print_board_reports(result.board_reports)
    _advance_focus_if_epic_closed(item, result)
    if result.cannot_assess:
        return EXIT_CANNOT_ASSESS
    return EXIT_OK if result.ok else EXIT_NOT_OK


def _advance_focus_if_epic_closed(item: dict, result: CloseOutResult) -> None:
    """Move the epic pin off an epic that just closed (epic #707, lane F5/#720).

    The epic-close ADVANCE is the one action a completed epic owes the board: a
    pinned focus outlives the epic it pins, so without this a fleet that finished
    an epic would keep resolving it forever. It runs only after a *successful*
    close of an item that is actually an epic, and it is reported either way -- a
    focus that could not be advanced is NOT-OK, never a silent pass. The decision
    itself is the brain's pure ``advance_focus``; this is only the trigger.
    """
    labels = item.get("labels") or []
    if "type:epic" not in labels or not result.ok:
        return
    try:
        import fleet.brain as brain  # noqa: PLC0415 — keeps the CLI import-light
    except ImportError as exc:  # pragma: no cover — the fleet package always ships
        print(f"focus: NOT-OK — the epic advance is unavailable ({exc})", file=sys.stderr)
        return
    moved, note = brain.advance_epic_focus()
    print(f"focus: {'OK' if moved else 'NOT-OK'} — {note}")
    if not moved:
        return
    print(f"focus: advanced off #{item.get('issue')} (epic closed)")


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
    audit_cmd.add_argument("--apply", action="store_true", help="file findings on the board (default: dry-run)")
    audit_cmd.set_defaults(func=cmd_audit)

    status_cmd = sub.add_parser("status", help="where one item sits, and what it still owes")
    status_cmd.add_argument("--issue", type=int, required=True)
    status_cmd.set_defaults(func=cmd_status)

    close_cmd = sub.add_parser("close", help="drive one item to hygiene, in dependency order")
    close_cmd.add_argument("--issue", type=int, required=True)
    close_cmd.add_argument("--evidence", default="", help="the evidence to record when closing")
    close_cmd.add_argument("--apply", action="store_true", help="file remaining findings on the board (default: dry-run)")
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
