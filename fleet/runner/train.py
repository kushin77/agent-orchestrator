"""train.py — the merge train: one fold, one verify, attribution by check
(issue #1411, parent #1295).

THE DEFECT THIS EXISTS FOR
    Per-head verification costs 15 minutes x N and re-breaks on every master
    flake, and the interactions that matter — a pair that is green alone and red
    together — only exist in the FOLDED tree, so no amount of per-head verifying
    can see them (#1254 step 6 measured four of those merges). The prototype
    (`~/ao-runner/train.sh` on the shared-services pair) landed 18 PRs in ONE
    squash (#1398 -> b8ad98f) after ONE verify and surfaced five
    green-alone-red-together interactions before landing, each fixed at its
    source PR in SECONDS by running the failing check on `master` and on
    `master + one PR` — never by bisecting 15 minutes of gate per head.

THE SHAPE (fold -> verify once -> attribute -> land)
    1. CANDIDATES. Every open, non-draft, MERGEABLE PR based on master that is
       not held, whose OWN body carries `Closes #<n>` lines, and none of whose
       closed issues is an EPIC. Every other PR is skipped BY NAME — a wave that
       silently drops a PR is the defect this repo keeps re-measuring.
    2. FOLD. Each candidate's fetched head is merged `--no-ff` onto the recorded
       master tip in ONE held worktree (`verify.HeldWorktree`: fd held for the
       whole run, removed by us, lessons 6/8 of #1343). A conflict is skipped by
       name (`conflict:<pr>:<path>`) and never aborts the wave.
    3. VERIFY ONCE, on the folded tree, with the park-retry the prototype's
       landing driver used (rc 10/11 is a permit, not a verdict).
    4. GREEN. Post `ao/gate-of-record` for the TRAIN HEAD and land through
       `scripts/merge-pr.sh` — the one guarded verb (lesson 4). The posted
       record is what the merged-tree seam reads back (`scripts/pr-queue.sh
       --check-merged-tree` requires a green gate-of-record for the head oid
       unless `AO_QUEUE_VERIFY_MERGED=1` is set), so the train verifies ONCE and
       the landing path still gets its merged-tree evidence: for a train, the
       merged tree IS the train head.
    5. RED. For each failing check, run THAT CHECK on master and on
       `master + PR_i` for the candidates whose diff touches the check's inputs,
       and report `red:<check> <- #<pr>`. The PRs it lands on are HELD and the
       rest are re-trained (bounded rounds).

WHAT THIS MODULE IS NOT
    It is not a second verification engine, a second poster or a second merge
    verb. The verify is `scripts/verify.sh` in a held worktree (the same script
    the runner runs), the post is `scripts/gate-status.sh` through
    `verify.real_post_status`, and the landing is `scripts/merge-pr.sh`. All
    three arrive as injected callables, so the tests drive this module with
    fakes and never touch a real repo, a real status or a real merge.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from fleet.runner.model import MERGE_VERB, OpenPR
from fleet.runner.verify import (
    MASTER_REFSPEC,
    PR_REFSPEC,
    Command,
    HeldWorktree,
    Ledger,
    Result,
    fetch_lock,
    gate_rc_of,
    retain_verify_evidence,
)

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

#: Where a train's own artifacts live, under the runner dir (runtime state).
TRAIN_DIR = "trains"

#: The park-retry the prototype's landing driver used (`/tmp/ao-train/land.sh`,
#: measured): `scripts/verify.sh` rc 10/11 means "the box-wide permit cap had no
#: slot", which is not a verdict about the tree. Bounded, because an unbounded
#: retry is how the 2026-09-18 storm started (AO-GR-21, issue #723).
PARK_ATTEMPTS = 8
PARK_SLEEP_SECONDS = 45.0
PARK_RCS = frozenset({10, 11})

#: How many times a red fold is re-trained after its attributed PRs are held.
#: Bounded for the same reason: a train that re-folds forever while a poisoned PR
#: keeps not being attributed is a loop nobody can stop.
MAX_ROUNDS = 3

#: How many unmerged paths a conflict refusal names. The FIRST few are what a
#: reader acts on; the full set is in the kept fold transcript.
MAX_CONFLICT_PATHS = 3

#: The check vocabulary: a failing check `<name>` is the script
#: `scripts/<name>.sh` (the gate runs its checks by that convention, and every
#: name the attestation carries resolves to one of them).
CHECK_SCRIPT_DIR = "scripts"

#: The declared gate-sensitive globs (issue #1054). Read LIVE, never copied:
#: `scripts/check-pr-contract.sh` cross-checks a PR's `Gate-changing:` line
#: against this list, so a train whose body declares `no` while the fold touches
#: one of these is refused by name at PR time.
GATE_PATHS_FILE = "scripts/lib/gate-paths.txt"

#: The body's OWN closing keywords. The train carries each folded PR's own lines
#: into its trailer — read from the PR BODY, never derived from a branch name
#: (#1266: branch-derived `Closes` closed nothing, 27 merges, 0 issues).
CLOSING_RE = re.compile(r"^\s*(?P<keyword>close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(?P<number>\d+)\s*$", re.IGNORECASE)
#: The reference line the shared trailer predicate requires INSIDE the trailing
#: block (`governance/isolation/trailer.py` -> `scripts/check-pr-contract.sh`).
CLASS_RE = re.compile(r"^\s*class:\s*(?P<rung>[A-Za-z0-9_-]+)\s*$", re.IGNORECASE)
#: The CMR quality ladder, highest last (`governance/conformance/policy.yaml` is
#: the authority; this is the ORDER, and an unknown rung outranks nothing).
CLASS_LADDER = ("template", "class", "pattern", "enterprise", "faang", "elite")

#: The EPIC label (`governance/dispatch/model.py`: `is_epic` is exactly this).
EPIC_LABEL = "type:epic"

#: A check that reads the WHOLE tree: every candidate touches its inputs.
WHOLE_TREE = ("**",)

#: The declared INPUTS of a check, by name — the paths a check reads, so the red
#: attribution only pays for the candidates that could have caused it (the
#: prototype's seconds-instead-of-a-bisect win). An UNDECLARED check is not
#: filtered: `check_inputs` returns None and every candidate is tested, because
#: skipping a PR on a guess about what a check reads is exactly how the real
#: cause is missed. Each entry is defensible from the check's own scan: the two
#: below walk every shell file in the tree.
CHECK_INPUTS: dict[str, tuple[str, ...]] = {
    "check-shell-patterns": ("**/*.sh",),
    "check-verdict-contains": ("**/*.sh",),
    "check-pr-runner": ("fleet/runner/**", "scripts/gate-status*", "scripts/merge-pr.sh", "scripts/pr-queue.sh"),
    "check-gate-status": ("scripts/gate-status*", "fleet/runner/**", "governance/platform/branch-protection.yaml"),
    "check-pr-contract": ("scripts/check-pr-contract.sh", "scripts/lib/gate-paths.txt", "**/*.sh"),
    "check-session-isolation": ("governance/isolation/**", "**/*.sh"),
}


# --- the records ---------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    """One PR the train may fold: everything the fold and the trailer need."""

    number: int
    head_sha: str
    title: str
    closes: tuple[str, ...]  # the PR's OWN closing lines, verbatim
    closed_issues: tuple[int, ...]
    class_rung: str = ""

    @property
    def ref(self) -> str:
        """The explicit refspec target its head is fetched under (lesson 8)."""
        return f"refs/remotes/origin/pr/{self.number}"


@dataclass(frozen=True)
class Skip:
    """A PR the train did NOT fold, BY NAME. Silence is never an outcome."""

    pr: int
    reason: str


@dataclass(frozen=True)
class Fold:
    """The folded tree: the base it was folded on, its head, and who is in it."""

    base_tip: str
    head: str
    folded: tuple[Candidate, ...]
    skipped: tuple[Skip, ...]
    body: str = ""

    @property
    def closes(self) -> tuple[str, ...]:
        """Every folded PR's own closing lines, deduped, in fold order."""
        out: list[str] = []
        for candidate in self.folded:
            for line in candidate.closes:
                if line not in out:
                    out.append(line)
        return tuple(out)

    @property
    def issues(self) -> tuple[int, ...]:
        out: list[int] = []
        for candidate in self.folded:
            for issue in candidate.closed_issues:
                if issue not in out:
                    out.append(issue)
        return tuple(out)


@dataclass(frozen=True)
class Attribution:
    """One failing check, and the candidate the train measured it back to."""

    check: str
    pr: int | None
    verdict: str  # pre-existing | attributed | unattributed | unrunnable | cannot-assess
    detail: str = ""

    def line(self) -> str:
        """`red:<check> <- #<pr>` — the finding the run reports and acts on."""
        if self.verdict == "attributed":
            return f"red:{self.check} <- #{self.pr}"
        if self.verdict == "pre-existing":
            return f"red:{self.check} <- pre-existing(master)"
        return f"red:{self.check} <- {self.verdict}"


@dataclass(frozen=True)
class TrainVerify:
    """The single verify of a fold."""

    rc: int
    state: str
    attempts: int
    posted: bool
    failing: tuple[str, ...] = ()
    summary: str = ""
    log: str = ""
    note: str = ""
    head: str = ""


# --- reading a PR body ----------------------------------------------------------
def closing_lines(body: str) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """The PR body's OWN closing lines and the issues they name, in order.

    Read from the body, never derived from a branch name — that is the whole
    point (#1266: 27 squash merges derived their `Closes` from nothing and
    closed 0 issues). The line is returned VERBATIM so the train's trailer
    carries the PR's own words; the number is what the epic refusal judges.
    """
    lines: list[str] = []
    numbers: list[int] = []
    for raw in (body or "").splitlines():
        match = CLOSING_RE.match(raw)
        if match is None:
            continue
        line = raw.strip()
        number = int(match.group("number"))
        if line not in lines:
            lines.append(line)
        if number not in numbers:
            numbers.append(number)
    return tuple(lines), tuple(numbers)


def declared_class(body: str) -> str:
    """The PR body's own `class:` declaration, when it carries one (warn-only)."""
    for raw in (body or "").splitlines():
        match = CLASS_RE.match(raw)
        if match is not None:
            return match.group("rung").lower()
    return ""


def highest_class(rungs: Iterable[str]) -> str:
    """The highest declared rung of the folded PRs; `pattern` when none declares.

    The train's diff is the union of its members' diffs, so the honest rung for
    the train is the highest one any member claimed. The block is warn-only
    today (issue #1328), which is why an absent declaration degrades to the
    ladder's middle rung rather than inventing `elite`.
    """
    ranked = [rung for rung in rungs if rung in CLASS_LADDER]
    return max(ranked, key=CLASS_LADDER.index) if ranked else "pattern"


# --- candidates -----------------------------------------------------------------
def labels_reader(gh: Command, slug: str, cache: dict[int, tuple[str, ...] | None] | None = None) -> Callable[[int], tuple[str, ...] | None]:
    """A cached `issue -> labels` reader. `None` means UNREADABLE, never empty.

    The distinction is load-bearing: the epic refusal must fail CLOSED. A reader
    that answered `()` for a failed call would let an epic-closing PR into the
    wave on the strength of an API error.
    """
    seen: dict[int, tuple[str, ...] | None] = cache if cache is not None else {}

    def labels_of(issue: int) -> tuple[str, ...] | None:
        if issue not in seen:
            result = gh(["api", f"repos/{slug}/issues/{issue}", "--jq", "[.labels[].name]"])
            if not result.ok:
                seen[issue] = None
            else:
                try:
                    seen[issue] = tuple(str(name) for name in json.loads(result.out or "[]"))
                except ValueError:
                    seen[issue] = None
        return seen[issue]

    return labels_of


def fold_candidates(
    prs: Sequence[OpenPR],
    bodies: dict[int, str | None],
    labels_of: Callable[[int], tuple[str, ...] | None],
    holds: dict[int, str],
) -> tuple[list[Candidate], list[Skip]]:
    """Split the open board into what the train folds and what it refuses, BY NAME.

    Order is the order `gh pr list` reported, so the fold is the same fold for
    the same board. Every refusal names the PR and the property that refused it.
    """
    candidates: list[Candidate] = []
    skipped: list[Skip] = []
    for pr in prs:
        if pr.draft:
            skipped.append(Skip(pr.number, f"draft:#{pr.number}"))
            continue
        if pr.mergeable == "CONFLICTING":
            skipped.append(Skip(pr.number, f"conflicting:#{pr.number}"))
            continue
        if pr.mergeable != "MERGEABLE":
            skipped.append(Skip(pr.number, f"mergeability-unknown:#{pr.number}:{pr.mergeable or 'UNKNOWN'}"))
            continue
        if pr.number in holds:
            skipped.append(Skip(pr.number, f"held:#{pr.number}:{holds[pr.number]}"))
            continue
        body = bodies.get(pr.number)
        if body is None:
            skipped.append(Skip(pr.number, f"body-unreadable:#{pr.number}"))
            continue
        closes, issues = closing_lines(body)
        if not closes:
            # A PR whose own body closes nothing would land with its issue still
            # open — the exact #1266 shape. It is refused here, not landed.
            skipped.append(Skip(pr.number, f"no-closes:#{pr.number}"))
            continue
        epic = None
        unreadable = False
        for issue in issues:
            names = labels_of(issue)
            if names is None:
                unreadable = True
                break
            if EPIC_LABEL in names:
                epic = issue
                break
        if unreadable:
            skipped.append(Skip(pr.number, f"labels-unreadable:#{pr.number}"))
            continue
        if epic is not None:
            skipped.append(Skip(pr.number, f"refused-epic:#{pr.number}:closes #{epic} ({EPIC_LABEL})"))
            continue
        candidates.append(
            Candidate(
                number=pr.number,
                head_sha=pr.head_sha,
                title=pr.title,
                closes=closes,
                closed_issues=issues,
                class_rung=declared_class(body),
            )
        )
    return candidates, skipped


# --- fetching and folding -------------------------------------------------------
def main_tip(git: Command, *, repo: Path) -> str | None:
    """`origin/master` as this clone sees it — the READ-ONLY half.

    Never the local checkout, which may itself be the stale side (AO-GR-25,
    issue #739). A caller that wants the tip to be fresh fetches first under the
    same lock `fetch_candidates`/`base_moved` use.
    """
    result = git(["rev-parse", "refs/remotes/origin/master"], cwd=repo)
    tip = result.out.strip() if result.ok else ""
    return tip or None


def fetch_candidates(git: Command, *, repo: Path, prs: Sequence[int], lock_path: Path) -> Result:
    """ONE fetch for master + every candidate head, with explicit refspecs.

    One call, under the fetch lock: N sequential fetches is N races on
    `cannot lock ref` (lesson 8 of #1343), and a train is exactly the run that
    would hit it N times.
    """
    refspecs = [MASTER_REFSPEC, *(PR_REFSPEC.format(pr=pr) for pr in prs)]
    with fetch_lock(lock_path):
        return git(["fetch", "--quiet", "origin", *refspecs], cwd=repo)


def unmerged_paths(git: Command, *, repo: Path) -> tuple[str, ...]:
    """The paths a failed merge left unmerged — what the refusal names."""
    result = git(["diff", "--name-only", "--diff-filter=U"], cwd=repo)
    return tuple(line.strip() for line in (result.out or "").splitlines() if line.strip())


def fold(
    candidates: Sequence[Candidate],
    *,
    repo: Path,
    git: Command,
    worktree: Path,
    base_tip: str,
    ledger: Ledger,
    train_id: str = "",
) -> Fold:
    """Merge every candidate HEAD onto `base_tip`, `--no-ff`, in one held worktree.

    What is merged is the SHA the candidate was verified against, after the
    fetched ref is confirmed to still BE that sha: a ref that moved between
    `gh pr list` and the fetch is a different tree, and folding it would land
    work nobody verified (`head-moved:<pr>`, skipped by name).

    Called INSIDE a `HeldWorktree` — the worktree is the caller's to hold and
    remove (lesson 6).
    """
    folded: list[Candidate] = []
    skipped: list[Skip] = []
    for candidate in candidates:
        resolved = git(["rev-parse", candidate.ref], cwd=repo)
        actual = resolved.out.strip() if resolved.ok else ""
        if resolved.rc != 0 or not actual:
            skipped.append(Skip(candidate.number, f"ref-missing:#{candidate.number}:{candidate.ref}"))
            continue
        if actual != candidate.head_sha:
            skipped.append(Skip(candidate.number, f"head-moved:#{candidate.number}:{candidate.head_sha[:12]}!={actual[:12]}"))
            continue
        merged = git(["merge", "--no-ff", "--no-edit", candidate.head_sha], cwd=worktree)
        if merged.rc != 0:
            paths = unmerged_paths(git, repo=worktree)
            git(["merge", "--abort"], cwd=worktree)
            named = ",".join(paths[:MAX_CONFLICT_PATHS]) or "unnamed"
            skipped.append(Skip(candidate.number, f"conflict:#{candidate.number}:{named}"))
            ledger.record("train-conflict", train_id=train_id, pr=candidate.number, sha=candidate.head_sha, paths=list(paths))
            continue
        folded.append(candidate)
        ledger.record("train-fold", train_id=train_id, pr=candidate.number, sha=candidate.head_sha, ok=True)
    head = git(["rev-parse", "HEAD"], cwd=worktree)
    return Fold(base_tip=base_tip, head=(head.out.strip() if head.ok else ""), folded=tuple(folded), skipped=tuple(skipped))


def round_branch(train_id: str, round_no: int) -> str:
    """The branch one round's fold is pushed as: `train/<stamp>-r<N>`.

    A round is a fold of a WAVE, and a wave that comes back red is re-trained
    without the PRs the red attributed to — that is a different tree, so it is a
    different branch and a different PR. Naming both after the round is what
    keeps a refused round's artifact readable instead of silently overwritten.
    """
    return train_id if round_no <= 1 else f"{train_id}-r{round_no}"


# --- the composed body ----------------------------------------------------------
def gate_paths(repo: Path) -> tuple[str, ...]:
    """The declared gate-sensitive globs, read live (never a second copy)."""
    try:
        raw = (Path(repo) / GATE_PATHS_FILE).read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#"))


def gate_changing_line(touched: Sequence[str], globs: Sequence[str]) -> str:
    """The `Gate-changing:` declaration, DERIVED from the fold's own diff.

    `scripts/check-pr-contract.sh` refuses a declared `no` whose diff touches a
    gate path AND a declared `yes` whose diff touches none, so a train that
    hardcoded either half would be refused on the wave where it was wrong. A
    train of gate fixes is the normal case, not the exception: the prototype
    folded gate scripts.
    """
    matched = sorted({path for path in touched if any(fnmatch.fnmatch(path, glob) for glob in globs)})
    if not matched:
        return "Gate-changing: no"
    return f"Gate-changing: yes — {', '.join(matched)}"


def compose_body(
    *,
    slug: str,
    train_id: str,
    fold: Fold,
    verify: TrainVerify | None,
    attributions: Sequence[Attribution],
    touched: Sequence[str],
    globs: Sequence[str],
    runtime: str = "Copilot agent (flash/train)",
) -> str:
    """The train PR's body: the fold's record, and the members' OWN closes lines.

    Shape rules this body must satisfy, all of them the repo's own and all of
    them checked at PR time or at merge time (`scripts/check-pr-contract.sh`,
    `scripts/check-squash-message.sh`):
      * `^Closes #<n>` — the members' own closing lines, verbatim;
      * `^AI-assistance: <runtime> (<model>/<mode>)` — a filled-in declaration;
      * `## Pre-existing red` — `None`, or a reproduced claim;
      * `Gate-changing:` — derived, because the cross-check refuses a guess;
      * a TRAILING trailer paragraph whose every line is a trailer line and
        which holds a `Refs <slug>#<n>` — a bare `Closes #n` is not enough for
        the shared predicate, which is why the members' lines are echoed with a
        `Refs` line each. Nothing may follow that paragraph.
    """
    conflicts = [skip for skip in fold.skipped]
    pre_existing = [a for a in attributions if a.verdict == "pre-existing"]
    attributed = [a for a in attributions if a.verdict == "attributed"]
    unresolved = [a for a in attributions if a.verdict not in ("pre-existing", "attributed")]

    lines: list[str] = []
    lines.append(f"Merge train `{train_id}`: one verified landing of {len(fold.folded)} pull request(s)")
    lines.append(f"folded onto `{fold.base_tip[:12]}` with `--no-ff`.")
    lines.append("")
    lines.append("## Closes")
    lines.append("")
    for line in fold.closes:
        lines.append(line)
    lines.append("")
    lines.append("## Classification")
    lines.append("")
    lines.append(f"class: {highest_class(candidate.class_rung for candidate in fold.folded)}")
    lines.append("posture: overall")
    lines.append("lifecycle: release")
    lines.append("pillar: governance")
    lines.append("pattern: GR-12")
    lines.append("lane: direct")
    lines.append("")
    lines.append("## What changed")
    lines.append("")
    lines.append(f"One fold of {len(fold.folded)} pull request(s) onto `master` {fold.base_tip[:12]}, verified ONCE")
    lines.append(f"as the tree `{fold.head[:12]}` and landed as one commit through `{MERGE_VERB}`.")
    lines.append("")
    lines.append("Included:")
    for candidate in fold.folded:
        lines.append(f"- #{candidate.number} — {candidate.title}")
    if conflicts:
        lines.append("")
        lines.append("Skipped by name:")
        for skip in conflicts:
            lines.append(f"- {skip.reason}")
    lines.append("")
    lines.append("## Merge order")
    lines.append("")
    lines.append(gate_changing_line(touched, globs))
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    lines.append("```")
    lines.append(f"$ python3 fleet/runner/cli.py train --apply   # train {train_id}")
    lines.append(f"folded: {len(fold.folded)} PR(s) onto {fold.base_tip[:12]} -> head {fold.head[:12]}")
    if verify is not None:
        lines.append("$ bash scripts/verify.sh verify   # inside the folded tree")
        lines.append(f"verify: {'PASS' if verify.rc == 0 else 'FAIL'} (rc {verify.rc}, attempts {verify.attempts}) {verify.summary}")
    lines.append(f"$ bash {MERGE_VERB} --pr <this PR>")
    lines.append("```")
    lines.append("")
    if attributed or unresolved:
        lines.append("## Attribution")
        lines.append("")
        for attribution in [*attributed, *unresolved]:
            lines.append(f"- `{attribution.line()}`" + (f" — {attribution.detail}" if attribution.detail else ""))
        lines.append("")
    lines.append("## AI-assistance")
    lines.append("")
    lines.append(f"AI-assistance: {runtime}")
    lines.append("")
    lines.append("## Clock invariant")
    lines.append("")
    lines.append("Clock-invariant: no time-pinned fixture — the train id is read from the live clock and every tree it names is measured, never seeded.")
    lines.append("")
    lines.append("## Pre-existing red")
    lines.append("")
    if pre_existing:
        for attribution in pre_existing:
            lines.append(f"`{attribution.line()}`")
        lines.append("")
        lines.append(f"Reproduce: `bash scripts/{pre_existing[0].check}.sh`")
        lines.append("")
        lines.append("```")
        lines.append(f"# on a clean checkout of {fold.base_tip[:12]}, with no member folded: the check is red")
        lines.append("```")
    else:
        lines.append("None — no failing gate is claimed to be pre-existing.")
    lines.append("")
    for issue in fold.issues:
        lines.append(f"Refs {slug}#{issue}")
    for line in fold.closes:
        lines.append(line)
    return "\n".join(lines) + "\n"


# --- the one verify --------------------------------------------------------------
def verify_once(
    *,
    git: Command,
    sh: Command,
    post_status: Callable[[str, int], Result],
    repo: Path,
    runner_dir: Path,
    worktree: Path,
    pr: int,
    head: str,
    ledger: Ledger,
    train_id: str = "",
    attempts: int = PARK_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
    sleep_seconds: float = PARK_SLEEP_SECONDS,
    timeout: float | None = None,
    pre_post: Callable[[], str | None] | None = None,
) -> TrainVerify:
    """Verify the folded tree ONCE, and post its gate-of-record when it is green.

    Called INSIDE the held worktree, exactly like `verify.run_verify`: `.verify/`
    is removed with the tree, so the transcript and the attestation are read here
    or lost (lesson 11 of #1343). The parked rc (10/11) is retried — it is a
    permit the box-wide cap did not have, not a verdict — and is NEVER posted
    (`gate_rc_of` returns None for it).

    `head` is the train head the record is about. `pre_post` is the guard the
    post passes through: it returns a refusal NAME (or None) and is asked AFTER
    the verify and BEFORE the post, which is where a base that moved has to be
    caught — a green published for a tree whose base is no longer the one it was
    folded on is stale evidence, and the train never lands on it.
    """
    waiter = sleep if sleep is not None else _sleep
    attempt = 0
    rc = CANNOT_ASSESS
    last = Result(CANNOT_ASSESS)
    for attempt in range(1, max(1, attempts) + 1):
        last = sh(["bash", "scripts/verify.sh", "verify"], cwd=worktree, timeout=timeout)
        rc = int(last.rc)
        if rc in PARK_RCS:
            ledger.record("train-parked", train_id=train_id, rc=rc, attempt=attempt, head=head)
            if attempt < attempts:
                waiter(sleep_seconds)
                continue
        break

    evidence: dict = {}
    try:
        # The run's OWN Result, not a rebuilt one: when the venue writes no
        # `.verify/verify.log` the tee'd stdout/stderr in it is the only
        # transcript there is, and `retain_verify_evidence` falls back to it.
        evidence = retain_verify_evidence(runner_dir=runner_dir, pr=pr, sha=head, worktree=worktree, result=last)
    except OSError as exc:
        evidence = {"evidence_note": f"evidence-retain-failed:{type(exc).__name__}"}

    state = _state_of(rc)
    gate_rc = gate_rc_of(rc)
    posted = False
    refusal = pre_post() if pre_post is not None else None
    if refusal:
        # The verify reached its verdict; the TREE no longer stands. The record is
        # kept and nothing is published: an unpostable green is not a green
        # (lesson 10) and the caller must re-fold.
        ledger.record("train-post-skipped", train_id=train_id, head=head, rc=rc, reason=refusal)
        evidence_note = ";".join(bit for bit in [str(evidence.get("evidence_note") or ""), refusal] if bit)
        evidence = {**evidence, "evidence_note": evidence_note}
    elif gate_rc is None:
        ledger.record("train-post-skipped", train_id=train_id, head=head, rc=rc, reason=f"not-a-verdict:rc{rc}")
    else:
        post = post_status(head, gate_rc)
        posted = post.ok
        ledger.record("train-post", train_id=train_id, head=head, rc=gate_rc, ok=post.ok, detail=(post.out or post.err).strip()[:200])

    record = TrainVerify(
        rc=rc,
        state=state,
        attempts=attempt,
        posted=posted,
        failing=tuple(str(name) for name in (evidence.get("failing_checks") or ())),
        summary=str(evidence.get("verify_summary") or ""),
        log=str(evidence.get("evidence_log") or ""),
        note=str(evidence.get("evidence_note") or ""),
        head=head,
    )
    ledger.record(
        "train-verify",
        train_id=train_id,
        head=head,
        rc=rc,
        state=state,
        attempts=attempt,
        posted=posted,
        failing_checks=list(record.failing),
        verify_summary=record.summary,
        evidence_log=record.log,
        evidence_note=record.note,
    )
    return record


def _state_of(rc: int) -> str:
    if rc == 0:
        return "green"
    if rc == 1:
        return "red"
    return "cannot-assess"


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def base_moved(git: Command, *, repo: Path, base_tip: str, lock_path: Path) -> str | None:
    """Re-read `origin/master`; the NEW tip when it moved, else None.

    The negative control this exists for: a base that moved between the fold and
    the landing must re-verify, never land stale evidence. Fetched under the same
    lock every other fetch uses, and compared against `refs/remotes/origin/
    master` — never against the local checkout, which may itself be the stale
    side (AO-GR-25, issue #739).
    """
    with fetch_lock(lock_path):
        git(["fetch", "--quiet", "origin", MASTER_REFSPEC], cwd=repo)
    tip = main_tip(git, repo=repo)
    if tip is None:
        return base_tip
    return tip if tip != base_tip else None


# --- attribution ----------------------------------------------------------------
def check_inputs(name: str, repo: Path) -> tuple[str, ...] | None:
    """The paths a check reads: the declared set, or None when undeclared.

    None is NOT "no inputs". It means this module cannot say, and the caller
    then tests EVERY candidate — an undeclared check is paid for, not skipped.
    A declared check that does not exist in the tree is `unrunnable` where it is
    measured, never silently green.
    """
    declared = CHECK_INPUTS.get(name)
    if declared is not None:
        return declared
    if (Path(repo) / CHECK_SCRIPT_DIR / f"{name}.sh").is_file():
        return WHOLE_TREE
    return None


def changed_paths(git: Command, *, repo: Path, base_tip: str, sha: str) -> tuple[str, ...] | None:
    """The candidate's own diff against the base (`A...B` = merge-base diff).

    None means the diff could not be read — and the caller treats that as
    "touches everything", because a filter that drops a candidate on a failed
    read is a filter that drops the cause.
    """
    result = git(["diff", "--name-only", f"{base_tip}...{sha}"], cwd=repo)
    if not result.ok:
        return None
    return tuple(line.strip() for line in (result.out or "").splitlines() if line.strip())


def pr_touches(paths: Sequence[str] | None, inputs: Sequence[str] | None) -> bool:
    """Does this diff touch the check's inputs? Unknown on either side = yes."""
    if inputs is None or paths is None:
        return True
    return any(fnmatch.fnmatch(path, glob) for path in paths for glob in inputs)


def run_check(sh: Command, worktree: Path, name: str) -> Result | None:
    """Run ONE check in a worktree. None when the tree carries no such script."""
    script = Path(worktree) / CHECK_SCRIPT_DIR / f"{name}.sh"
    if not script.is_file():
        return None
    return sh(["bash", f"{CHECK_SCRIPT_DIR}/{name}.sh"], cwd=worktree)


def attribute_red(
    failing: Sequence[str],
    candidates: Sequence[Candidate],
    *,
    repo: Path,
    git: Command,
    sh: Command,
    runner_dir: Path,
    base_tip: str,
    ledger: Ledger,
    train_id: str = "",
) -> list[Attribution]:
    """Which candidate reds each failing check? `master` vs `master + one PR`.

    The prototype's technique, and the reason the train is affordable: a check is
    seconds, a full gate is 15 minutes, so the attribution costs seconds per
    candidate instead of a bisect per head.

    A check that is RED on master ALONE is `pre-existing` — the wave does not
    pay for it and the finding says so in its own words (the PR-body section
    exists for exactly that claim). Otherwise the candidates whose diff touches
    the check's inputs are tried in fold order, each in its own held worktree at
    `base_tip` with that single head merged `--no-ff`, and the FIRST that reds is
    the attribution — recorded as such, with the ones not tried named by the
    `stopped-at-first` detail rather than implied to be clean.
    """
    attributions: list[Attribution] = []
    pending: list[str] = []
    if not failing:
        return attributions

    base_wt = Path(runner_dir) / TRAIN_DIR / "reasons" / "base"
    with HeldWorktree(git, repo=repo, path=base_wt, sha=base_tip) as held_base:
        for check in failing:
            result = run_check(sh, held_base.path, check)
            if result is None:
                attributions.append(Attribution(check, None, "unrunnable", detail=f"{CHECK_SCRIPT_DIR}/{check}.sh is not in the base tree"))
                ledger.record("train-attr", train_id=train_id, check=check, verdict="unrunnable")
                continue
            if result.rc == 1:
                attributions.append(Attribution(check, None, "pre-existing", detail="red on the base alone"))
                ledger.record("train-attr", train_id=train_id, check=check, verdict="pre-existing")
                continue
            if result.rc != 0:
                attributions.append(Attribution(check, None, "cannot-assess", detail=f"rc{result.rc} on the base alone"))
                ledger.record("train-attr", train_id=train_id, check=check, verdict="cannot-assess", rc=result.rc)
                continue
            pending.append(check)

        wanted: dict[str, list[Candidate]] = {}
        for check in pending:
            inputs = check_inputs(check, repo)
            if inputs is None:
                ledger.record("train-attr-note", train_id=train_id, check=check, detail="inputs-undeclared: every candidate is tested")
            wanted[check] = [
                candidate
                for candidate in candidates
                if pr_touches(changed_paths(git, repo=repo, base_tip=base_tip, sha=candidate.head_sha), inputs)
            ]

        ordered: list[Candidate] = []
        for check in pending:
            for candidate in wanted[check]:
                if all(candidate.number != seen.number for seen in ordered):
                    ordered.append(candidate)

        trees: dict[int, HeldWorktree] = {}
        # A candidate whose `master + PR_i` tree could not even be BUILT (the
        # single head conflicts with the base) is not a tree any check can be
        # measured in: testing it would attribute a red to a PR whose merge is
        # not the tree the train failed on.
        unbuilt: set[int] = set()
        try:
            for candidate in ordered:
                path = Path(runner_dir) / TRAIN_DIR / "reasons" / f"{candidate.number}-{candidate.head_sha[:12]}"
                held = HeldWorktree(git, repo=repo, path=path, sha=base_tip)
                try:
                    held.__enter__()
                except RuntimeError as exc:
                    unbuilt.add(candidate.number)
                    ledger.record("train-attr", train_id=train_id, pr=candidate.number, verdict="cannot-assess", detail=str(exc)[:200])
                    continue
                trees[candidate.number] = held
                merged = git(["merge", "--no-ff", "--no-edit", candidate.head_sha], cwd=held.path)
                if merged.rc != 0:
                    git(["merge", "--abort"], cwd=held.path)
                    unbuilt.add(candidate.number)
                    ledger.record("train-attr", train_id=train_id, pr=candidate.number, verdict="conflict")

            for check in pending:
                verdict = None
                for candidate in wanted[check]:
                    held = trees.get(candidate.number)
                    if held is None or candidate.number in unbuilt:
                        continue
                    result = run_check(sh, held.path, check)
                    if result is None:
                        verdict = Attribution(check, None, "unrunnable", detail=f"{CHECK_SCRIPT_DIR}/{check}.sh is not in {candidate.head_sha[:12]}")
                        break
                    if result.rc == 1:
                        tried = [c.number for c in wanted[check]]
                        stopped = [n for n in tried[tried.index(candidate.number) + 1 :]]
                        detail = f"red on master+#{candidate.number}"
                        if stopped:
                            detail += f"; not tried after the first red: {','.join(f'#{n}' for n in stopped)}"
                        verdict = Attribution(check, candidate.number, "attributed", detail=detail)
                        break
                    if result.rc != 0:
                        verdict = Attribution(check, None, "cannot-assess", detail=f"rc{result.rc} on master+#{candidate.number}")
                        break
                if verdict is None:
                    verdict = Attribution(check, None, "unattributed", detail="no candidate's diff reproduced it")
                attributions.append(verdict)
                ledger.record("train-attr", train_id=train_id, check=check, pr=verdict.pr, verdict=verdict.verdict, detail=verdict.detail)
        finally:
            for held in trees.values():
                held.__exit__(None, None, None)

    return attributions


def held_prs(attributions: Sequence[Attribution]) -> list[tuple[int, str]]:
    """The PRs a red attributes to, with the check that convicts each."""
    out: list[tuple[int, str]] = []
    for attribution in attributions:
        if attribution.verdict == "attributed" and attribution.pr is not None:
            out.append((int(attribution.pr), f"{attribution.line()} (train verify)"))
    return out


def abandon_round(gh: Command, slug: str, pr: int, *, reason: str, ledger: Ledger, train_id: str = "") -> bool:
    """Close a round's own PR when that round will not land, NAMING why.

    A round that is refused leaves a real PR behind (the fold was pushed and the
    head was verified — that is the record). Leaving it open would be the board
    claiming a wave is in flight when the train has already moved on, so it is
    closed with the reason; the fold, the verify and the attribution all stay in
    the ledger and in the body it was closed with.
    """
    result = gh(
        [
            "pr",
            "close",
            str(pr),
            "--repo",
            slug,
            "--comment",
            f"Round abandoned by the merge train `{train_id}`: {reason}. Nothing was merged for this round.",
        ]
    )
    ledger.record("train-abandon", train_id=train_id, pr=pr, ok=result.ok, reason=reason)
    return bool(result.ok)


# --- the ledger view ------------------------------------------------------------
def train_rows(rows: Sequence[dict]) -> list[dict]:
    """Every row a train wrote (the ledger is shared; the train's rows are named)."""
    return [row for row in rows if str(row.get("event", "")).startswith("train")]


def train_lines(rows: Sequence[dict]) -> list[str]:
    """One line per train the ledger knows: what folded, what was refused, why."""
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for row in train_rows(rows):
        train_id = str(row.get("train_id") or "(unattributed)")
        if train_id not in grouped:
            grouped[train_id] = []
            order.append(train_id)
        grouped[train_id].append(row)

    lines: list[str] = []
    for train_id in order:
        events = grouped[train_id]
        by_event: dict[str, list[dict]] = {}
        for row in events:
            by_event.setdefault(str(row.get("event")), []).append(row)
        start = by_event.get("train-start", [{}])[0]
        folded = [row for row in events if row.get("event") == "train-fold"]
        skipped = [row for row in events if row.get("event") == "train-skip"]
        verify = by_event.get("train-verify", [{}])[-1]
        land = by_event.get("train-land", [{}])[-1]
        end = by_event.get("train-end", [{}])[-1]
        bits = [f"train {train_id}"]
        bits.append(f"apply={start.get('apply')} at {start.get('at')}")
        bits.append(f"folded:{len(folded)}")
        if skipped:
            bits.append("skipped:" + ", ".join(str(row.get("reason")) for row in skipped))
        if by_event.get("train-conflict"):
            bits.append("conflicts:" + ", ".join(f"#{row.get('pr')}" for row in by_event["train-conflict"]))
        if verify:
            bits.append(f"verify={verify.get('state')} rc={verify.get('rc')} attempts={verify.get('attempts')} posted={verify.get('posted')}")
            failing = [str(name) for name in (verify.get("failing_checks") or [])]
            if failing:
                bits.append(f"failing:{','.join(failing)}")
            if verify.get("verify_summary"):
                bits.append(f"why:{verify['verify_summary']}")
            if verify.get("evidence_log"):
                bits.append(f"log:{verify['evidence_log']}")
        for row in by_event.get("train-attr", []):
            if row.get("verdict") == "attributed":
                bits.append(f"red:{row.get('check')} <- #{row.get('pr')}")
            elif row.get("verdict") not in (None,):
                bits.append(f"red:{row.get('check')} <- {row.get('verdict')}")
        for row in by_event.get("train-hold", []):
            bits.append(f"held:#{row.get('pr')}")
        if land:
            bits.append(f"landed:#{land.get('pr')}->{str(land.get('train_sha') or '')[:12]}")
        if by_event.get("train-base-moved"):
            bits.append(f"base-moved:{by_event['train-base-moved'][-1].get('detail')}")
        if end:
            bits.append(f"rc={end.get('rc')} state={end.get('state')}")
        lines.append("  " + " | ".join(bits))
    return lines
