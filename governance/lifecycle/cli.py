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
import time
from pathlib import Path
from typing import Callable, Sequence

#: The repository whose state the lifecycle reads and writes: the journals other
#: modules read as landing records, the lane records, the decision ledger, and the git
#: object store the evidence is measured from. Derived from this file's location, so a
#: checkout's own copy operates on its own state.
ROOT_ENV = "AO_LIFECYCLE_ROOT"


def lifecycle_root() -> Path:
    """The state root — this checkout's, unless ``AO_LIFECYCLE_ROOT`` names another.

    The seam exists because the driver has two requirements that a lane worktree
    cannot satisfy at once, and the box measured the cost of that (2026-09-18, #1247):

    * it must run the **fixed** code — and the fix for this class
      (:meth:`GhOps._measure_landed_tree`, #1003) is on ``master``; and
    * it must run against the **fleet's** state — the journals, lane records and
      ledger the fleet loops write in the shared checkout, without which the item is
      not even in the audit's scope.

    The shared checkout is not a stable code baseline: it holds whichever branch a
    lane last left it on. Measured on this box while diagnosing #1247, it sat on
    ``issue-708-wire-runaway-guard``, 3.7 hours behind ``origin/master``, so its copy
    of this module still refuses a red frozen head outright and the #1003 remedy could
    not be exercised from it at all. Pointing a lane's copy — the one with the fix — at
    the shared checkout's ``.fleet`` is the alternative to editing the shared checkout,
    which dozens of lanes are using.

    A root that is not a repository is refused rather than used: the override decides
    where landing records are written, and a typo there would scatter them. An empty or
    unset override is this checkout, unchanged.
    """
    override = os.environ.get(ROOT_ENV, "").strip()
    local = Path(__file__).resolve().parents[2]
    if not override:
        return local
    candidate = Path(override).expanduser().resolve()
    if not (candidate / ".git").exists():
        raise SystemExit(
            f"{ROOT_ENV}={override} is not a repository root (no .git at {candidate}): "
            "the lifecycle writes the journals, lane records and decision ledger under it, "
            "so an unvalidated override would scatter another module's landing records "
            "outside the fleet's state"
        )
    return candidate


ROOT = lifecycle_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.lifecycle import directive, gate, ledger, live  # noqa: E402
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


#: The composite gate's own per-check record, written by ``scripts/verify.sh`` into the
#: worktree it ran in — **even on failure**, deliberately ("so a red run still carries
#: evidence"). It is the only machine-readable account of *which* check the gate
#: disagreed with: the gate's own FAIL banner names how many failed and which ones
#: were **skipped**, never which ones failed.
ATTESTATION_PATH = Path(".verify") / "attestation.json"

#: The gate's own tri-state for a check, read from that record: 0 PASS, 2
#: CANNOT-ASSESS (the gate records it as SKIP), anything else NOT-OK. These are the
#: gate's contract, quoted from ``scripts/verify.sh`` rather than re-derived from its
#: human-readable summary.
CHECK_PASS = 0
CHECK_SKIP = 2


def _head_of(worktree: Path) -> str:
    """The commit a worktree holds, or ``""`` when there is no tree to read."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def failed_checks(worktree: Path, since: float) -> list[str]:
    """The checks the gate ran in ``worktree`` and disagreed with, by name.

    A refused verification carries the gate's own sentence — ``verify: FAIL (2 of 142
    checks failed, 4 skipped: module-registry, ...)`` — which names the **skips** and
    never the failures. The operator is then told that something is wrong and not
    what, and the only way to learn more is to run the whole composite gate again, so
    the finding cannot be acted on and cannot converge. Measured on #627 and #629:
    their close-outs refused with "1 of 142 checks failed" and "2 of 142 checks
    failed" and nothing else, and the two board findings that record it (#1247,
    #1251) were unactionable for exactly that reason.

    The names come from the gate's **own attestation**, and only when the file is
    demonstrably this run's:

    * its ``git_sha`` must be the measured tree's HEAD — a lane keeps the attestation
      of an older run in the same worktree, and that is a measurement of another
      commit;
    * it must have been written at or after ``since``, the attempt's own start, so a
      run that died before writing one cannot be described by the previous run's red.

    When either guard fails nothing is returned, and the caller reports the count it
    did measure rather than inventing a name — the substitution this module exists to
    prevent. Names keep the gate's own order, so the first failure is the first the
    operator reads.
    """
    path = worktree / ATTESTATION_PATH
    try:
        if path.stat().st_mtime < since:
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict) or str(payload.get("git_sha") or "") != _head_of(worktree):
        return []
    entries = payload.get("checks")
    if not isinstance(entries, list):
        return []
    names = [
        str(entry.get("name") or "")
        for entry in entries
        if isinstance(entry, dict) and entry.get("rc") not in (CHECK_PASS, CHECK_SKIP)
    ]
    return [name for name in names if name]


def _prefix(names: Sequence[str]) -> str:
    """``failing check(s): a, b — `` , or ``""`` when no name could be read.

    The names go **first** in a refusal because ``closeout`` clamps a step's detail at
    300 characters and the gate's own banner already spends most of that on counts and
    skips: a failure that cannot name its check cannot be acted on, and the name is the
    only way to reproduce it without running the whole composite gate again.
    """
    return f"failing check(s): {', '.join(names)} — " if names else ""


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


def commit_is_contained(worktree: Path, ancestor: str, descendant: str) -> bool:
    """Does ``descendant`` contain ``ancestor`` — is the work in that tree at all?

    The relation a lane needs when its pull request was **squash-merged** (#1098). The
    squash creates a *new* commit on the default branch, so the branch tip the merge
    replaced is not an ancestor of it, and a lane cut from the default branch (the
    correct venue, rule 15) can never *equal* the verified head commit. It does
    **contain** the commit the merge landed as — and the default branch still contains
    that commit, so a gate run in such a lane measures the current tree, which is the
    only tree whose repo-wide invariants are meaningful.

    ``merge-base --is-ancestor`` exits 1 for "no" and 128 when the repository cannot
    answer at all (a commit it does not hold, no such path). Both are *not contained*:
    this fails closed, because a control that cannot fail is a formality (GR-12).
    """
    if not ancestor or not descendant:
        return False
    result = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def tree_relation(worktree: Path, left: str, right: str) -> str:
    """``"same"``, ``"different"`` or ``"unknown"`` — how two commits' trees compare.

    Three answers rather than two, because *"the trees differ"* and *"the trees cannot
    be read"* are different facts and only one of them licenses a conclusion (#1149).
    ``git diff --quiet`` exits 128 for an object this repository does not hold; a caller
    that read that as *different* would report a drift nobody measured, and one that read
    it as *same* would admit a tree nobody compared. Both are fail-open, and this module
    exists to close exactly that gap.
    """
    if not left or not right:
        return "unknown"
    result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--quiet", left, right],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "same"
    if result.returncode == 1:
        return "different"
    return "unknown"


def trees_are_identical(worktree: Path, left: str, right: str) -> bool:
    """Do two commits carry the **same tree** — is this the tree that landed?

    The link that licenses measuring a lane which does not equal the verified commit.
    "What matters is that the tree which was verified is the tree that landed"
    (``governance/lifecycle/README.md``): a squash merge preserves the tree, so the
    landing's tree and the verified head's tree are the *same object*. Without a check
    like this one, ``commit_is_contained`` alone would admit a lane that contains a
    landing built from **different** content — a measurement of work nobody verified.

    It is one of the ways :func:`landing_carries_change` answers — the exact case, where
    nothing landed between the branch cut and the merge — and since #1298 it is no longer
    the *only* one: a base that moved under the squash makes the whole trees differ
    necessarily, so requiring equality was a control that could never fire.

    Only the measured ``same`` is a match, and an unreadable commit is not one, so this
    fails closed too — the tri-state is read through :func:`tree_relation` rather than
    re-derived here, so the two cannot drift apart.
    """
    return tree_relation(worktree, left, right) == "same"


def _merge_base(worktree: Path, left: str, right: str) -> str:
    """The commit ``left`` and ``right`` diverge from, or ``""`` when there is none.

    ``git merge-base`` answers "no common ancestor" with exit 1 and an unresolvable
    commit with 128; both are *no base*, and the caller then falls back to the question
    that needs no ancestry at all. It never fails **open**: a base nobody could resolve
    cannot license a lane.
    """
    result = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", left, right],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _change_paths(worktree: Path, base: str, commit: str) -> list[str] | None:
    """The paths ``commit`` changed against ``base``, or ``None`` when unreadable.

    ``None`` and ``[]`` are different facts — "I could not compare" against "there is no
    change here" — and :func:`change_relation` reads them differently rather than
    conflating them into one answer its caller would have to guess at.
    """
    result = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", base, commit],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def change_relation(worktree: Path, verified: str, landing: str) -> str:
    """``"same"``, ``"different"`` or ``"unknown"``: does the landing carry the verified change?

    The second half of the admissibility rule, restated so that it can *fire* (#1298).
    "The tree which was verified is the tree that landed" is exactly true only when
    nothing else landed on the default branch between the branch cut and the merge — and
    a **squash merge composes its landing from the base AT MERGE TIME**, so a sibling
    landing in that window puts content into the landing the branch tip never had.
    Whole-tree equality is then *unsatisfiable* for the very case the arm exists for: a
    control that cannot fire, the inverse of GR-12.

    Measured on this repository's own history (issue #1298): branch tip ``17dc00a``
    carries a change that landed as ``870eb26`` — the same ``git patch-id --stable``, the
    same twelve paths, identical content at every one of them — while
    ``git diff --quiet 17dc00a 870eb26`` is not clean and the tip is not an ancestor of
    the landing. The arm that exists to admit a squash-merged item could not admit it.

    What the arm is really asking is whether the landing carries **the verified work**, so
    the question is asked that way. Three answers are ``"same"``, and each answers a shape
    the others cannot:

    * the verified commit is **in the landing's history** — the merge-commit form, where
      the landing descends from the very commit that was gated;
    * the landing carries the **tip's own change**: every path ``verified`` changed
      against its merge base with the landing resolves to the *same blob* in the landing,
      so everything the verified commit added, altered or removed is present and
      unchanged. The landing's other content is the sibling landings that moved the base —
      precisely what a squash merge necessarily carries, and what the arm must tolerate;
    * the **whole trees are identical** — the original #1098 case, asked here only when
      the path-by-path comparison had nothing to compare, because it needs no ancestry.

    ``"unknown"`` is every question that could not be answered — an unreadable commit, no
    common base, a change nothing could read. It is not ``"same"``: a difference nobody
    measured may not admit a lane, and may not be reported as one either.
    """
    if not verified or not landing:
        return "unknown"
    if commit_is_contained(worktree, verified, landing):
        return "same"
    base = _merge_base(worktree, verified, landing)
    paths = _change_paths(worktree, base, verified) if base else None
    if paths:
        result = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--quiet", verified, landing, "--", *paths],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return "same"
        if result.returncode != 1:
            return "unknown"
        return "different"
    # No common base, a change that could not be read, or a change that is empty. The
    # whole-tree question is the one left, and it needs no ancestry at all: asked here it
    # can only *add* an admission where the path comparison had nothing to compare.
    return "same" if trees_are_identical(worktree, verified, landing) else "unknown"


def landing_carries_change(worktree: Path, verified: str, landing: str) -> bool:
    """Is the change ``verified`` brought present, unchanged, in ``landing``?

    The boolean the admissibility arm reads, beside :func:`trees_are_identical` — which it
    subsumes but no longer requires, because *requiring* it is what made the arm unfireable
    for a squash merge whose base moved (#1298). Only a measured ``"same"`` is a match; the
    tri-state is read through :func:`change_relation` rather than re-derived here, so the
    two cannot drift apart.
    """
    return change_relation(worktree, verified, landing) == "same"


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


class RedGate(RuntimeError):
    """The gate ran at a tree and a check failed: a *measured* red (#1003).

    A ``RuntimeError``, so every reader of a failed step keeps seeing a failure and
    every refusal this port has always raised stays a refusal. It is a distinct class
    so that one caller can ask a question the message cannot answer: *this tree* is red
    is a different statement from *this tree could not be measured*, and only the first
    is recoverable — by measuring the tree that landed — for an item whose pull request
    is already merged.

    ``verdict_line`` is the gate's own *sentence* — its verdict line, not its whole
    transcript — kept beside the message because ``closeout`` truncates a step's detail at
    300 characters: a *composed* refusal that has to name two red trees must fit inside
    that cap, and the sentence saying which run failed is the part worth spending it on.
    ``failing`` is carried as *data* for the same reason and by the same rule as the
    message: the gate's own sentence says how many checks failed and which ones were
    skipped, never **which** ones failed, and a refusal that cannot name the failing
    check cannot be acted on. Keeping it a field rather than a prefix of the sentence
    lets each refusal put it where the cap cannot reach it.
    """

    def __init__(
        self, detail: str, verdict_line: str = "", failing: Sequence[str] = ()
    ) -> None:
        super().__init__(detail)
        self.verdict_line = verdict_line
        self.failing = tuple(failing)

    def named(self) -> str:
        """``failing check(s): a, b`` — or ``""`` when nothing was readable."""
        return f"failing check(s): {', '.join(self.failing)}" if self.failing else ""


class GhOps:
    """The real effects close-out needs, through ``gh`` and the repo's own CLIs."""

    def __init__(self, root: Path | None = None, record: dict | None = None) -> None:
        self.root = root or ROOT
        #: The lifecycle record the close-out was collected from. The directive's
        #: terminal move is gated on whether the order's change has landed, and
        #: that fact comes from the board read that already happened — not from a
        #: second, possibly different, read of the world (#821). It is also where the
        #: item's pull request number comes from, so the merged tree can be found
        #: (#1003).
        self.record = record

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        result = subprocess.run(args, cwd=str(cwd or self.root), capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-200:] or "command failed")
        return (result.stdout or "").strip()

    def merge_pull_request(self, number: int) -> str:
        # Refuse BEFORE merging if the composed squash message would drop the
        # ticket trailer (#1102) — named squash-message-would-drop-trailer so
        # the refusal matches scripts/check-squash-message.sh's own vocabulary.
        check = subprocess.run(
            ["bash", str(self.root / "scripts" / "check-squash-message.sh"), "--pr", str(number)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            raise RuntimeError(
                f"PR {number} is squash-message-would-drop-trailer: "
                f"scripts/check-squash-message.sh --pr {number} exited {check.returncode}; "
                f"{(check.stdout or check.stderr).strip()[-300:]}"
            )
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

        A **failure** names the checks it measured red (:func:`failed_checks`), because
        the gate's own banner names only how many failed and which ones skipped. Without
        them the refusal says that something is wrong and not what, and the only way to
        find out is to run the whole composite gate again — which is why the two board
        findings measured on #627 and #629 (#1247, #1251) carried "1 of 142 checks
        failed" and "2 of 142 checks failed" and could not be acted on. The names are
        never guessed: an attestation that is not demonstrably this run's yields no
        names, and the message then reports the count it did measure.
        """
        started = time.time()
        run = gate.run_gate(lambda: _gate_attempt(worktree))
        if run.admitted:
            return
        if run.cannot_assess:
            raise gate.CannotAssess(run.verdict, run.detail(), run.remediation())
        failing = failed_checks(worktree, started)
        red = RedGate(
            f"{_prefix(failing)}{run.detail()} — the gate ran against {described} and reported a "
            "failure, so the item has no green verification",
            # The *short* sentence, not ``detail()``: a composed refusal has to fit the
            # 300-character step detail, and the retry bookkeeping is not what an operator
            # needs to read twice.
            run.final.headline() or f"verify: {run.verdict.upper()} (rc {run.exit_code})",
            failing,
        )
        raise red

    def tree_relation(self, left: str, right: str) -> str:
        """How two commits' trees compare — read in the repository this port measures.

        The driver asks this to decide *which* commit a merged item's evidence is against
        (#1149). It is a question about a repository, so it is asked of the port rather
        than of a bare ``git`` call inside the driver — the driver owns the decision, the
        port owns knowing where to look.
        """
        return tree_relation(self.root, left, right)

    def commit_is_superset(self, larger: str, smaller: str) -> bool:
        """Does ``larger``'s tree carry every path ``smaller``'s does, unchanged?

        Read as a content diff, not an ancestry check: a squash merge disconnects the
        branch head from the landing either way, so ``merge-base --is-ancestor`` can
        never answer this. ``git diff --diff-filter=DM`` between the two, restricted to
        deletions and modifications, is empty exactly when nothing ``smaller`` carries
        was removed or changed on the way to ``larger`` — additions are the only
        difference a genuinely-advanced branch can produce. An unreadable pair answers
        ``False``: nothing is claimed about a comparison nobody could measure.
        """
        if not larger or not smaller:
            return False
        result = subprocess.run(
            ["git", "-C", str(self.root), "diff", "--no-renames", "--diff-filter=DM", "--name-only", smaller, larger],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        return result.stdout.strip() == ""

    def _refusal_reason(self, worktree: Path, head: str, commit: str, landing: str) -> str:
        """Why this lane may not stand for ``commit`` — one line, naming the true reason.

        #1098's refusal said "and does not contain it" for two different causes, so the
        #977/#978 shape — a lane that **does** contain the landing, whose landing carries
        a different tree than the commit the evidence names — was diagnosed as the one
        thing it was not. The causes have different remedies: a lane of the wrong tree is
        re-cut, while a subject naming a tree that never landed is re-pointed. Naming them
        apart is the whole point of a refusal whose operator has to act on it (#1149).
        """
        opening = f"lane head {head[:12]} is not the verified commit {commit[:12]}"
        if not landing:
            return (
                f"{opening}: no landing is recorded for it, so a lane cut from the default "
                "branch cannot stand for it — the equality arm is the only arm for an "
                "unmerged (or landless) pull request"
            )
        if not commit_is_contained(worktree, landing, head):
            return (
                f"{opening} and does not contain the landing {landing[:12]}, the commit the squash "
                "landed as: a lane may stand for the verified commit only when it contains the "
                "landing and that landing carries the verified work (#1098)"
            )
        if change_relation(worktree, commit, landing) == "same":
            return (
                f"{opening}: the lane contains the landing {landing[:12]} and that landing "
                "carries the change the verified commit introduced, so these facts do not "
                "explain the refusal — the admissibility rule refused a lane it should admit"
            )
        return (
            f"{opening}: it DOES contain the landing {landing[:12]}, but the landing carries "
            f"neither the verified tree nor the change {commit[:12]} introduced — {commit[:12]} "
            "names a tree that never landed, so it may not be the subject of this item's "
            f"evidence; the tree that landed is the one {landing[:12]} carries (#1149)"
        )

    def _admissible(self, worktree: Path, head: str, commit: str, landing: str) -> str:
        """``"equals"``, ``"contains"`` or ``""`` — how this lane may stand for ``commit``.

        Two ways, and only two (#1098):

        * **equals** — the lane IS the verified commit. The unchanged case.
        * **contains** — the pull request was **squash-merged**, so the verified head
          commit is not an ancestor of anything on the default branch; the commit the
          merge *landed as* is, and a lane cut from the default branch contains it.
          Admitted only when **both** halves hold: the lane contains the landing
          (``commit_is_contained``) **and** the landing carries the verified work
          (:func:`landing_carries_change`). The second half is what keeps this honest —
          without it a lane containing a landing built from other content would be
          measured as if it proved this item.

          It is asked as a **change**, not as whole-tree equality (#1298). Equality is
          satisfiable only while nothing else lands on the default branch between the
          branch cut and the merge, because a squash merge composes its landing from the
          base *at merge time*; the moment a sibling lands, the arm cannot fire for the
          very case it exists for — a control that cannot fire, the inverse of GR-12.
          What is required is unchanged in substance: the lane must contain the landing,
          and the landing must carry what was verified. Containing *a* landing is not the
          claim; containing *the verified work* is.

        ``landing`` is non-empty only for a **merged** pull request, which is the whole
        reason the second arm is unreachable for an item still in flight: an unmerged
        item's verified commit is its head, and its lane must be *at* it.
        """
        if not commit:
            return "equals"  # no verified commit named: the legacy lane path, unchanged
        if head == commit:
            return "equals"
        if landing and commit_is_contained(worktree, landing, head) and landing_carries_change(
            worktree, commit, landing
        ):
            return "contains"
        return ""

    def _in_detached_tree(self, commit: str, measure: Callable[[Path], None]) -> None:
        """Run ``measure`` in a throwaway detached worktree at ``commit``, then remove it.

        Extracted from :meth:`_remeasure` (#1003) because there are now two commits
        worth measuring this way — the verified head commit once the lane is gone (#786),
        and the merge commit once the head's tree is red (#1003) — and a second copy of
        the scratch/cleanup dance is a second place for a leaked worktree. The tree is
        destroyed either way, including when ``measure`` raises, so a measurement can
        never itself become a lane.
        """
        root_dir = scratch_root()
        root_dir.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="ao-lifecycle-verify-", dir=str(root_dir)))
        try:
            self._run(["git", "-C", str(self.root), "worktree", "add", "--detach", str(scratch), commit])
            measure(scratch)
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

    def record_verification(self, issue: int, commit: str, landing: str = "", drifted: str = "") -> str:
        """Record a green attestation for ``commit``, from the lane, from the commit,
        from a drifted head's landing (#1149), or — when the frozen head is red and
        the item is merged — from the merged tree (#786, #1098, #1149, #1003).


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

        A lane that is not *at* ``commit`` is admitted only when ``landing`` — the
        commit a **squash merge** landed as — is contained by it and carries the verified
        work (:func:`landing_carries_change`): the verified tree itself when nothing else
        landed in between, or the change ``commit`` introduced once a sibling landing
        moved the base under the squash (#1098, #1298). The record then names **all
        three**: ``commit`` (the verified commit the evidence is against, whose meaning is
        unchanged), ``landing`` and ``measured`` (the tree the gate actually ran in), with
        ``via: "contains"``.
        ``commit`` deliberately keeps naming the *verified* commit rather than the
        measured tree: that is the convention the audit, the invariant's own text, the
        README table and every existing record use, and re-pointing it would have
        silently invalidated each of them. The lane is the measurement; the verified
        commit is the subject; the record says which is which.

        ``drifted`` is the pull request's **live** head where a commit landed *after* the
        squash carried it. ``governance/lifecycle/closeout.py`` measured that its tree is
        not the landed one, so it cannot be the subject and the landing is (#1149). It is
        recorded as ``drifted_head`` rather than dropped: the whole defect was that the
        drifted head was used *silently*, as though it were the verified work. It changes
        nothing about what is accepted — the lane is still admitted by the two arms only,
        with the tree half intact.

        When that frozen head's tree is **red** and the pull request is already merged,
        the tree that *landed* is measured instead, and the record says so (#1003).
        Measured on #955: seven close-out attempts over two hours, every one
        ``REMAINS VERIFY_EVIDENCE_MISSING``, for work whose merged tree was green. A
        commit is immutable, so re-running the head can never converge — but the merge
        commit is a different commit, and for an already-merged item it is the tree the
        change actually landed as. See :meth:`_measure_landed_tree` for why that cannot
        launder a red lane.

        A run that did not happen writes **no journal**. ``.fleet/lifecycle``'s
        presence is another module's landing record — ``governance/reconcile``
        reads a journal file as "this issue's work landed" — so writing one for a
        parked run would let capacity be read as a landing, the substitution
        golden rule 17 forbids. The attempts are recorded where close-out prints
        them instead; nothing carries them as evidence.
        """
        try:
            measured, source, via = self._measure_verified_head(issue, commit, landing)
        except RedGate as red:
            measured, source, via = self._measure_landed_tree(issue, commit, red), "merged-tree", ""
        # The attestation names the **verified head** — the subject the audit holds it
        # against — and, only when it is a different commit, the tree the gate actually
        # ran in. For the lane and the reclaimed-commit venues the two are the same, so
        # those records are exactly the ones #786 has always written. The squash-landing
        # arm (#1098) is the one exception: it always names ``landing`` and ``measured``
        # alongside ``via``, because the tree that proved this item is not ``commit``'s own.
        subject = str(commit or measured)
        verify = {"ok": True, "commit": subject, "source": source}
        if via == "contains":
            verify.update({"landing": landing, "measured": measured, "via": via})
            if drifted and drifted != commit:
                # The live head whose tree never landed (#1149). Disclosed precisely so
                # the substitution that caused this defect can never be silent again.
                verify["drifted_head"] = drifted
        elif measured != subject:
            verify["measured"] = measured
        if source == "reclaimed-lane" and drifted and drifted != commit and commit == landing:
            # A subject that is the landing rather than the branch tip (#1149): the
            # re-measurement is still a real gate run at the commit the evidence names,
            # and the drift is still disclosed rather than dropped.
            verify.update({"landing": landing, "drifted_head": drifted})
        write_journal(issue, {"verify": verify}, self.root)
        if source == "merged-tree":
            return (
                f"verify green at {measured[:12]} — measured in the tree that landed, because the frozen "
                f"branch head {subject[:12]} is red and the merge commit is the tree the change landed as"
            )
        if via == "contains":
            # Which half admitted the lane is said out loud, because the two are not the
            # same evidence: "the landing carries the verified tree" is the #1098 case, and
            # "the landing carries the change the verified commit introduced" is the one a
            # base that moved under the squash forces (#1298). A reader of the step should
            # not have to re-derive which of them this record came from.
            carried = (
                "the landing carries the verified tree"
                if self.tree_relation(commit, landing) == "same"
                else "the landing carries the change the verified commit introduced"
            )
            return (
                f"verify green at {subject[:12]} (measured in the lane at {measured[:12]}, "
                f"which contains the landing {landing[:12]}; {carried})"
                + (
                    f"; the live head {drifted[:12]} advanced past the squash and its tree never "
                    "landed, so the evidence is against the tree that landed"
                    if verify.get("drifted_head")
                    else ""
                )
            )
        if source == "reclaimed-lane":
            return f"verify green at {measured[:12]} (re-measured at the verified commit; the lane is gone)"
        return f"verify green at {measured[:12]}"

    def _measure_verified_head(self, issue: int, commit: str, landing: str = "") -> tuple[str, str, str]:
        """Gate the verified head commit — in the lane, or at the commit (#786, #1098).

        Returns ``(the commit whose tree was gated, where the run was taken, "contains"
        when the lane stood in for ``commit`` via the squash-landing arm else "")``.
        Raises ``gate.CannotAssess`` when nothing was measured (a park, an unusable
        permit store, a signal) and :class:`RedGate` when the gate measured a failing
        tree — two outcomes the caller must be able to tell apart, because only the
        second is answered by measuring another tree (#1003).
        """
        records = _lane_records(self.root).get(issue) or []
        lane = select_lane(records, commit)
        if lane is not None and lane["worktree_exists"]:
            worktree = Path(lane["worktree"])
            head = self._run(["git", "-C", str(worktree), "rev-parse", "HEAD"])
            via = self._admissible(worktree, head, commit, landing)
            if not via:
                raise RuntimeError(self._refusal_reason(worktree, head, commit, landing))
            self._gate_in(worktree, head[:12])
            return head, "lane", via
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
        return self._remeasure(issue, commit, dead=lane), "reclaimed-lane", ""

    def _measure_landed_tree(self, issue: int, commit: str, red: RedGate) -> str:
        """Measure the tree that actually landed, when the frozen head's is red (#1003).

        A commit is immutable, so a red branch head stays red however many times it is
        re-run — measured on #955, where seven attempts over ~2 hours refused identically
        while the *merged* tree was green. The head ``4ed3fc0`` was committed at
        16:18:30 and did not contain ``72dca6c``, which had landed at 16:16:48; the squash
        ``494ff91`` was composed at 16:19:38 **with ``72dca6c`` as its parent**, so the
        tree that landed held a declaration the frozen branch head never had. The
        question the invariant really asks — *did the change that landed reach a green
        gate?* — is answerable at the **merge commit**, and only there: for a squash
        merge no other commit in the object store holds the landed tree.

        What bounds it, so this can never launder a red lane:

        * the head is always measured **first**, and its tree is the strongest evidence
          whenever it is green — this runs only after it is measured red;
        * the merge commit is the item's **own** pull request's, read from the item's
          record or live from GitHub, so it contains the change under test: a change that
          is genuinely broken is red there too;
        * it is only tried when the landed tree is a **different commit** from the one
          already measured — so no tree is gated twice, and the common case, where the
          head and the merge commit are the same tree, costs nothing;
        * a park is not a red: ``CannotAssess`` is never answered by measuring
          elsewhere;
        * and when the landed tree is red as well, the original red is raised, naming
          both trees. The invariant stands.
        """
        landed = self._merged_commit(issue)
        if not commit or not landed or landed == commit:
            raise red
        try:
            self._in_detached_tree(landed, lambda scratch: self._gate_in(scratch, landed[:12]))
        except gate.CannotAssess:
            raise
        except RedGate as also_red:
            # Composed from each run's own sentence rather than their whole messages: this
            # becomes a step's detail, which closeout truncates at 300 characters, and the
            # fact an operator needs is *which* two trees are red — not the retry bookkeeping
            # twice over.
            raise RuntimeError(
                f"{_prefix(red.failing)}{red.verdict_line or red} — the gate ran against {commit[:12]} "
                "and reported a failure, so the item has no green verification — and the tree that "
                f"landed ({landed[:12]}) is red too ({_prefix(also_red.failing)}"
                f"{also_red.verdict_line or 'the gate failed'})"
            ) from None
        return landed

    def _item(self, issue: int) -> dict:
        """The item this close-out was collected for, out of the record it was given."""
        for entry in (self.record or {}).get("items") or []:
            if int(entry.get("issue") or 0) == issue:
                return entry
        return {}

    def _merged_commit(self, issue: int) -> str:
        """The merge commit of the item's pull request, or ``""`` when it is not merged.

        The record is read first, because it costs nothing: an item collected *after* its
        merge already carries the commit. A live read is the fallback, and it is needed
        because close-out collects the item **before** it merges anything — step 1 merges,
        step 2 records — so the record this run holds still says the pull request is open,
        and the live read is the only one that can answer "is it merged *now*".

        An unreadable answer is ``""``, never a guess: a red head then stands, unchanged,
        which is the honest outcome.
        """
        pr = (self._item(issue) or {}).get("pr") or {}
        recorded = str(pr.get("merge_commit") or "")
        if recorded and str(pr.get("state") or "").lower() == "merged":
            return recorded
        number = int(pr.get("number") or 0)
        if not number:
            return ""
        try:
            view = self._run(["gh", "pr", "view", str(number), "--json", "state,mergeCommit"])
            payload = json.loads(view)
        except (RuntimeError, json.JSONDecodeError):
            return ""
        if str(payload.get("state") or "").upper() != "MERGED":
            return ""
        return str((payload.get("mergeCommit") or {}).get("oid") or "")

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
        self._in_detached_tree(resolved, lambda scratch: self._gate_in(scratch, resolved[:12]))
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
    # #1266: subjects whose OWN issue is already closed, read from this same
    # record — `board_report_findings` uses it to refuse filing a fresh
    # VERIFY_EVIDENCE_MISSING board issue against work nobody can act on
    # without reopening the issue first, and to resolve one already filed.
    closed_subjects = frozenset(
        f"#{item.get('issue')}" for item in record.get("items") or [] if str(item.get("state") or "").lower() == "closed"
    )
    board_reports = board_report_findings(findings, _reporter(), apply=args.apply, closed_subjects=closed_subjects)
    if not args.json:
        _print_board_reports(board_reports)
    print(f"lifecycle-hygiene: FAIL ({len(report['findings'])} finding(s))", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_status(args: argparse.Namespace) -> int:
    if not args.live and args.issue is None:
        print("status: CANNOT-ASSESS — pass --issue <n> or --live", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    record = collect_from_github()
    if args.live:
        # The live feed (issue #885): every in-scope item's stage, projected from
        # the SAME record `status` (without `--live`) and `audit` already read —
        # not a second collection, so the projection cannot drift from what those
        # verbs saw. `live.check_live` (the gate's drift provocation) fails loudly
        # if this ever changes to read a second, possibly stale, source.
        print(json.dumps(live.project(record), indent=2))
        return EXIT_OK
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
    _record_close_decision(args.issue, result)
    if result.cannot_assess:
        return EXIT_CANNOT_ASSESS
    return EXIT_OK if result.ok else EXIT_NOT_OK


def _record_close_decision(issue: int, result: CloseOutResult) -> None:
    """One append-only ledger record per close-out call (issue #885).

    ``ok``/``cannot-assess`` are ``decision`` records (nothing was refused; a
    park is a decision to wait, not a defect); a remaining finding is a
    ``refusal``, named by the first invariant code still owed so the trail can
    be grepped by code the same way the board report is.
    """
    subject = f"#{issue}"
    if result.remaining:
        code = result.remaining[0].code
        ledger.record_decision(
            ROOT, action="close", subject=subject, outcome=ledger.OUTCOME_REFUSED,
            detail=describe(result), code=code,
        )
        return
    ledger.record_decision(
        ROOT, action="close", subject=subject, outcome=ledger.OUTCOME_OK, detail=describe(result),
    )


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


def _live_issue_closed(root: Path, issue: int) -> bool | None:
    """The issue's closed-ness read from LIVE GitHub — ``None`` when unreachable.

    ``retire`` gates on CLOSED, not on the lifecycle record's ``landed`` fact
    (#861): a superseded issue never carries a change of its own, so it is
    outside the record :func:`consume` reads and the record cannot answer this
    question. GitHub itself can, so it is read directly here, the same way
    ``fleet/terminal.py``'s ``gh_issue_field`` reads a live field rather than the
    (possibly stale) committed board snapshot.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue), "--json", "state", "-q", ".state"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    state = (result.stdout or "").strip().upper()
    return state == "CLOSED" if state else None


def cmd_retire(args: argparse.Namespace) -> int:
    """Retire a stranded ``sent/`` directive for a superseded (closed, no change of
    its own) issue — the second terminal mode ``directive.py`` names (#861).

    ``--closed`` overrides the live GitHub read for a scripted/offline caller (a
    test, or an operator who already confirmed it another way); by default the
    issue's live state is read via ``gh issue view`` because a superseded issue
    is, by construction, outside the lifecycle record :func:`consume` uses.
    """
    root = Path(args.root) if args.root else ROOT
    record = directive.resolve(root, args.directive)
    if record is None:
        print(f"retire: unknown directive {args.directive!r}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if args.closed is not None:
        closed = args.closed
    else:
        closed = _live_issue_closed(root, record.issue) if record.issue is not None else None
        if closed is None:
            print(
                f"retire: CANNOT-ASSESS — could not read #{record.issue}'s live state from GitHub "
                "(pass --closed explicitly to override)",
                file=sys.stderr,
            )
            return EXIT_CANNOT_ASSESS
    try:
        detail = directive.retire(
            root,
            args.directive,
            closed=closed,
            reason=args.reason,
            superseded_by=tuple(args.superseded_by),
        )
    except directive.DirectiveRefused as exc:
        print(f"retire REFUSED: {exc}", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"retire: {detail}")
    return EXIT_OK


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
    status_cmd.add_argument("--issue", type=int, default=None, help="required unless --live is given")
    status_cmd.add_argument(
        "--live", action="store_true",
        help="project every in-scope item's stage instead of one issue's status (issue #885)",
    )
    status_cmd.set_defaults(func=cmd_status)

    close_cmd = sub.add_parser("close", help="drive one item to hygiene, in dependency order")
    close_cmd.add_argument("--issue", type=int, required=True)
    close_cmd.add_argument("--evidence", default="", help="the evidence to record when closing")
    close_cmd.add_argument("--apply", action="store_true", help="file remaining findings on the board (default: dry-run)")
    close_cmd.set_defaults(func=cmd_close)

    collect_cmd = sub.add_parser("collect", help="write the live lifecycle record")
    collect_cmd.add_argument("--out", required=True)
    collect_cmd.set_defaults(func=cmd_collect)

    retire_cmd = sub.add_parser(
        "retire",
        help="retire a sent/ directive for a superseded issue (closed, no change of its own — #861)",
    )
    retire_cmd.add_argument("--directive", required=True, help="the directive id in .fleet/sent/")
    retire_cmd.add_argument("--reason", required=True, help="why the order is dead (e.g. 'superseded')")
    retire_cmd.add_argument(
        "--superseded-by", dest="superseded_by", type=int, nargs="+", required=True,
        help="the issue number(s) whose change actually did the work",
    )
    retire_cmd.add_argument(
        "--closed", dest="closed", action="store_const", const=True, default=None,
        help="assert closed-ness rather than reading it live from GitHub",
    )
    retire_cmd.add_argument("--root", default="", help="repository root (default: this checkout)")
    retire_cmd.set_defaults(func=cmd_retire)
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
