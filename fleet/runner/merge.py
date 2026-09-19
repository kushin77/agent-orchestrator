"""merge.py — the PR runner's merge transport (issue #1343).

TWO SEAMS, IN ORDER, AND NOTHING ELSE
  1. `bash scripts/pr-queue.sh --check-merged-tree <pr> --head <sha>
     --against-base origin/master` — the merged-tree evidence seam (#1332,
     lesson 3 / #1254 step 6): master + PR must be green TOGETHER at the
     moment of merging. It is CONSUMED here, never reimplemented: a checkout
     whose `scripts/pr-queue.sh` lacks the flag is `merged-tree-seam-missing`
     (CANNOT-ASSESS) and nothing merges.
  2. `bash scripts/merge-pr.sh --pr <pr>` — the ONE guarded merge verb
     (lesson 4, #1266): it runs `scripts/check-squash-message.sh` first, so a
     body whose last paragraph is not the trailer is refused
     `squash-message-would-drop-trailer`, and `Closes #<n>` is derived there.
     This module never spells the raw GitHub merge command.

AFTER A MERGE
  The new master tip is classified by the shared trailer predicate
  (`governance.isolation.trailer.classify_commit`); a finding is recorded as
  `landed-tip-noncompliant:<sha>` and the runner STOPS merging for the rest of
  the cycle (the prototype's "stop hard").

Dry-run by default (`apply=False`): the seam runs, the verb runs in ITS dry-run
mode, and nothing merges. Every path writes a ledger row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fleet.runner.model import MERGE_VERB, MERGED_TREE_SEAM
from fleet.runner.verify import Command, Ledger, Result, fetch_lock, MASTER_REFSPEC

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

SEAM_FLAG = "--check-merged-tree"
REFUSAL_RE = re.compile(r"(merged-tree-unverified:\S+|merged-tree-red:\S+|squash-message-would-drop-trailer)")


@dataclass(frozen=True)
class MergeOutcome:
    pr: int
    sha: str
    rc: int  # 0 merged (or dry-run OK) / 1 refused / 2 cannot-assess
    merged: bool
    reason: str = ""
    new_tip: str | None = None
    stop: bool = False  # the landed tip failed the trailer predicate: stop merging


def seam_available(repo: Path) -> bool:
    """Does this checkout's pr-queue.sh carry the merged-tree seam (#1332)?"""
    script = Path(repo) / "scripts" / "pr-queue.sh"
    try:
        return SEAM_FLAG in script.read_text(encoding="utf-8")
    except OSError:
        return False


def named_refusal(text: str, default: str) -> str:
    match = REFUSAL_RE.search(text or "")
    return match.group(1) if match else default


def run_merge(
    pr: int,
    sha: str,
    *,
    repo: Path,
    sh: Command,
    git: Command,
    ledger: Ledger,
    apply: bool,
    runner_dir: Path,
    classify_tip: Callable[[str], str | None] | None = None,
    verify_merged_locally: bool = True,
) -> MergeOutcome:
    repo = Path(repo)

    # 1. the merged-tree seam ------------------------------------------------
    if not seam_available(repo):
        reason = f"merged-tree-seam-missing:{pr}"
        ledger.record("refuse", pr=pr, sha=sha, reason=reason, via=MERGED_TREE_SEAM)
        return MergeOutcome(pr, sha, CANNOT_ASSESS, False, reason)

    env = {"AO_QUEUE_VERIFY_MERGED": "1" if verify_merged_locally else "0"}
    seam = sh(
        ["bash", "scripts/pr-queue.sh", SEAM_FLAG, str(pr), "--head", sha, "--against-base", "origin/master"],
        cwd=repo,
        env=env,
    )
    if seam.rc != 0:
        reason = named_refusal(seam.err + seam.out, f"merged-tree-unverified:{pr}")
        ledger.record("refuse", pr=pr, sha=sha, reason=reason, via=MERGED_TREE_SEAM, rc=seam.rc)
        return MergeOutcome(pr, sha, CANNOT_ASSESS if seam.rc == 2 else NOT_OK, False, reason)
    ledger.record("merged-tree", pr=pr, sha=sha, ok=True, detail=(seam.out or "").strip()[-200:])

    # 2. the guarded verb -----------------------------------------------------
    verb = sh(["bash", MERGE_VERB, "--pr", str(pr)], cwd=repo, env={"AO_MERGE_APPLY": "1" if apply else "0"})
    if verb.rc == 1:
        reason = named_refusal(verb.err + verb.out, f"merge-refused:{pr}")
        ledger.record("refuse", pr=pr, sha=sha, reason=reason, via=MERGE_VERB, rc=1)
        return MergeOutcome(pr, sha, NOT_OK, False, reason)
    if verb.rc != 0:
        reason = f"merge-cannot-assess:{pr}:rc{verb.rc}"
        ledger.record("refuse", pr=pr, sha=sha, reason=reason, via=MERGE_VERB, rc=verb.rc)
        return MergeOutcome(pr, sha, CANNOT_ASSESS, False, reason)
    if not apply:
        ledger.record("merge-dry-run", pr=pr, sha=sha, via=MERGE_VERB)
        return MergeOutcome(pr, sha, OK, False, f"dry-run:{pr}")

    # 3. the landed tip must classify clean -----------------------------------
    with fetch_lock(Path(runner_dir) / "fetch.lock"):
        git(["fetch", "--quiet", "origin", MASTER_REFSPEC], cwd=repo)
    tip = git(["rev-parse", "refs/remotes/origin/master"], cwd=repo)
    new_tip = tip.out.strip() if tip.ok else None
    stop = False
    finding = None
    if classify_tip is not None and new_tip:
        try:
            finding = classify_tip(new_tip)
        except Exception as exc:  # PredicateUnavailable and friends: unproven is not clean
            finding = f"predicate-unavailable:{str(exc)[:120]}"
        if finding:
            stop = True
            ledger.record("stop", pr=pr, sha=sha, reason=f"landed-tip-noncompliant:{new_tip[:12]}:{finding}")
    ledger.record("merge", pr=pr, sha=sha, via=MERGE_VERB, new_tip=new_tip, finding=finding)
    return MergeOutcome(pr, sha, OK, True, f"merged:{pr}", new_tip, stop)


def real_classify_tip(repo: Path) -> Callable[[str], str | None]:
    """The shared predicate, imported lazily so the tests never need it."""

    def classify(sha: str) -> str | None:
        import sys

        root = str(Path(repo))
        if root not in sys.path:
            sys.path.insert(0, root)
        from governance.isolation.trailer import classify_commit  # noqa: WPS433 (lazy on purpose)

        return classify_commit(repo, sha)

    return classify


__all__ = ["run_merge", "MergeOutcome", "seam_available", "named_refusal", "real_classify_tip", "Result"]
