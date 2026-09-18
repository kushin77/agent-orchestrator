"""Is this artifact's *work* already on the default branch? (#1291)

The worktree/branch audit (``audit.py``, issue #628) explains an artifact by a
session beat, a claim record, or the *landing history* — and the landing history
was only ever the close-out journal ``.fleet/lifecycle/<issue>.json``, which is
**gitignored runtime state**. Measured at ``origin/master 99f6b37`` (2026-09-18,
issue #1291): of 35 real-tree findings, 23 were lane branches and 12 worktrees,
and 25 of them were artifacts whose work **was already on the default branch** —
reported as orphans only because no journal happened to exist for them. Every
one of those false findings reds the composite gate, which on this box is a
fleet-wide serialization point (16 open pull requests).

The repository does know that a lane landed. The evidence is its **own tracked
history** — the default branch's commits — and this module is how the audit
reads it. A lane is LANDED when the *work*, not the name, is on the default
branch, proven three ways, strongest first:

1. **ancestry** — ``git merge-base --is-ancestor <tip> <default-ref>``: the tip
   itself is on the default branch (a merge-commit or a fast-forward landing).
2. **tree containment** — every path the tip changed (against its merge base
   with the default branch) resolves to the *same blob* on the default branch,
   so the tip's own diff is already there. This is the shape a **squash merge**
   leaves behind, where the tip is deliberately *not* an ancestor of anything
   (the same failure mode ``AGENTS.md`` rule 16 records for verification).
3. **patch identity** — some commit **on the default branch** carries the same
   ``git patch-id --stable`` as the tip's combined diff. Candidate commits are
   generated from the default branch's own subjects (``Closes #n`` / ``#n`` —
   the fleet's landing convention), and the **proof is the patch, never the
   name**: a branch whose work is not on the default branch has no matching
   patch, whatever it is called. This is what separates a landed lane from an
   abandoned one whose *issue number* merely appears in history.

## What it deliberately does NOT do

* **No wildcard.** An artifact is never excused because a record somewhere names
  its issue, and never because its branch name looks like a merged one. A branch
  whose tip is not an ancestor of the default branch stays a finding *unless* one
  of the artifact-level proofs above holds — measured: of the 23 branch findings
  at ``99f6b37``, 13 are proven landed and **10 stay findings** (three of which
  the repository's own reclaim tooling independently keeps as
  ``head-not-merged``).
* **No proof from a dirty worktree.** A worktree is only explained by landed work
  when it holds **nothing uncommitted** (``git status --porcelain -uall`` is
  empty): the proofs speak about *committed* work, and uncommitted work is by
  definition on no branch at all. Measured: 11 of the 12 worktree findings carry
  uncommitted files, so they stay findings.
* **It removes nothing.** Like every other read in this package, this is a read:
  it names artifacts, it never reclaims one.

## Fail-closed, in both directions

* ``git`` itself being unreadable raises :class:`LandingUnavailable` — "I could
  not look" is **not** "not landed", and the audit turns it into CANNOT-ASSESS.
* The default-branch ref being absent (a scratch repository, a fixture root) is
  *no landing evidence available*, which can only ever **add** findings: an
  excuse that cannot be proven is never granted. Every proof here is an excuse,
  so failing to prove one can never turn a finding into a pass.
* A patch candidate list that is longer than :data:`MAX_CANDIDATE_COMMITS` is not
  searched further: past that bound the issue number has stopped narrowing
  anything, and the artifact stays unexplained rather than being excused by a
  name.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: The default branch, by its **remote** ref: rule 22 — "drift is measured
#: against the remote, and never fails open" — because the local checkout may
#: itself be the stale side.
DEFAULT_REF = "origin/master"

WORKTREE = "worktree"
BRANCH = "branch"

_ISSUE_BRANCH = re.compile(r"issue-(\d+)")
_ISSUE_REFERENCE = re.compile(r"#(\d+)")

#: How many candidate landing commits to patch-compare for one artifact.
MAX_CANDIDATE_COMMITS = 40

#: The longest a single ``git`` call may take before it is treated as unreadable.
GIT_TIMEOUT_SECONDS = 60


class LandingUnavailable(Exception):
    """``git`` could not be read at all — never to be read as "not landed"."""


def _issue_of(text: str) -> int | None:
    match = _ISSUE_BRANCH.match(text or "")
    return int(match.group(1)) if match else None


class RepoLanding:
    """Prove a lane's work is on the default branch, from a real repository.

    One instance resolves the default-branch ref once and memoizes what it
    learns (the candidate landing commits, their patch ids, the default branch's
    own commits), because :func:`proof` is called once per unexplained artifact.
    """

    def __init__(self, root: Path | str, *, ref: str = DEFAULT_REF) -> None:
        self.root = Path(root)
        # A repository that git cannot even locate is CANNOT-ASSESS, not "clean".
        probe = self._run(["rev-parse", "--git-dir"])
        if probe is None:
            raise LandingUnavailable(f"{self.root}: git rev-parse --git-dir failed")
        resolved = self._run(["rev-parse", "--verify", "--quiet", ref])
        # No default-branch ref => no landing evidence exists here (a fixture
        # root, a repository with no remote). Documented, and fail-closed: it can
        # only add findings.
        self.ref = ref if resolved else None
        self._candidates: dict[int, list[str]] | None = None
        self._patch_ids: dict[str, str | None] = {}
        #: (tip, issue) -> proof. A lane's branch and its worktree share a tip, and
        #: several artifacts can share one landed commit: measured on the real
        #: tree, 142 unmatched artifacts produced 944 candidate commits and 38
        #: patch ids, so the candidates are worth memoizing.
        self._proofs: dict[tuple[str, int | None], str] = {}
        #: Every commit the default branch can reach, read ONCE. Ancestry is then a
        #: set membership instead of one `merge-base --is-ancestor` per artifact —
        #: measured at ~19ms a call on this repository, which is the whole cost of
        #: the proof for the ~50% of artifacts that have not landed.
        self._reachable: set[str] | None = None

    # -- git ----------------------------------------------------------------

    def _run(self, args: list[str], *, cwd: Path | str | None = None) -> str | None:
        """Run one read-only git command; ``None`` when it did not produce an answer."""
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd or self.root), *args],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout if result.returncode == 0 else None

    def _patch_id(self, diff_text: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "patch-id", "--stable"],
                input=diff_text,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return result.stdout.split()[0]

    # -- the artifact's tip -------------------------------------------------

    def _tip(self, kind: str, name: str, branch: str) -> str:
        if kind == WORKTREE:
            head = self._run(["rev-parse", "HEAD"], cwd=name)
            return (head or "").strip()
        if kind == BRANCH:
            ref = name or branch
            tip = self._run(["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"])
            return (tip or "").strip()
        return ""

    def _uncommitted(self, path: str) -> int:
        """How many paths this worktree holds that no branch has. -1 when unreadable."""
        listing = self._run(["status", "--porcelain", "-uall"], cwd=path)
        if listing is None:
            return -1
        return len([line for line in listing.splitlines() if line.strip()])

    # -- the three proofs ---------------------------------------------------

    def _ancestry(self, tip: str) -> bool:
        """Is the tip itself on the default branch?

        Answered from one `rev-list` of the default branch rather than a
        `merge-base --is-ancestor` per artifact; that call is the fallback when
        the listing cannot be read, so a failure here can only cost time, never
        an excuse (an ancestry this cannot prove is simply not granted).
        """
        if self._reachable is None:
            listing = self._run(["rev-list", self.ref])
            self._reachable = set(listing.split()) if listing is not None else set()
        if self._reachable:
            return tip in self._reachable
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "merge-base", "--is-ancestor", tip, self.ref],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def _changed_paths(self, tip: str) -> list[str]:
        """The paths this tip changed, against its merge base with the default branch.

        The three-dot form asks git for that merge base itself, so this is one
        call rather than two — the same diff `merge-base` + `diff` would produce.
        """
        listing = self._run(["diff", "--name-only", f"{self.ref}...{tip}"])
        if listing is None:
            return []
        return [line for line in listing.splitlines() if line.strip()]

    def _contained(self, tip: str, paths: list[str]) -> bool:
        """Every path the tip changed is byte-identical on the default branch."""
        if not paths:
            return False
        result = self._run(["diff", "--quiet", self.ref, tip, "--", *paths])
        # `--quiet` prints nothing: rc 0 (no differences) is the only success.
        return result is not None

    def _candidate_commits(self) -> dict[int, list[str]]:
        """The default branch's own commits, keyed by every ``#n`` in the subject.

        This is the fleet's landing convention read as a *candidate generator*:
        a squash landing names its issue and its pull request in the subject. The
        name never proves anything here — :func:`_patch_id` does.
        """
        if self._candidates is not None:
            return self._candidates
        listing = self._run(["log", "--format=%H%x1f%s", self.ref])
        by_issue: dict[int, list[str]] = {}
        for line in (listing or "").splitlines():
            sha, _, subject = line.partition("\x1f")
            if not sha:
                continue
            for number in _ISSUE_REFERENCE.findall(subject):
                bucket = by_issue.setdefault(int(number), [])
                if len(bucket) < MAX_CANDIDATE_COMMITS:
                    bucket.append(sha)
        self._candidates = by_issue
        return by_issue

    def _commit_patch_id(self, sha: str) -> str | None:
        if sha not in self._patch_ids:
            # `--root` so an initial commit is a diff too; a merge commit has no
            # patch of its own (`diff-tree -p` prints nothing) and is skipped.
            diff = self._run(["diff-tree", "--root", "-p", "--no-color", "--no-commit-id", sha])
            self._patch_ids[sha] = self._patch_id(diff) if diff else None
        return self._patch_ids[sha]

    def _patch_identity(self, tip: str, issue: int | None) -> str | None:
        if not issue:
            return None
        # Three-dot again: the patch is the tip's own change, not master's drift.
        diff = self._run(["diff", "--no-color", f"{self.ref}...{tip}"])
        if not diff:
            return None
        wanted = self._patch_id(diff)
        if not wanted:
            return None
        for sha in self._candidate_commits().get(issue, []):
            if self._commit_patch_id(sha) == wanted:
                return sha
        return None

    # -- the answer ---------------------------------------------------------

    def proof(self, kind: str, name: str, branch: str = "") -> str:
        """``""`` when not proven landed; else ``landed:<how>:<sha>``.

        The returned string is the audit's *evidence*, so it names how it was
        proven and which commit proved it — never "the name matched".
        """
        if self.ref is None:
            return ""
        tip = self._tip(kind, name, branch)
        if not tip:
            return ""
        candidate = self._candidate_proof(kind, name, branch, tip)
        if not candidate:
            return ""
        if kind == WORKTREE:
            # Applied to EVERY proof, not only containment: they all speak about
            # *committed* work, and a venue holding uncommitted changes holds work
            # that is on no branch at all. Measured cost control: the check runs
            # only for a worktree whose committed work would otherwise be excused.
            if self._uncommitted(name) != 0:
                return ""
        return candidate

    def _candidate_proof(self, kind: str, name: str, branch: str, tip: str) -> str:
        """The strongest proof this tip's committed work is on the default branch."""
        issue = _issue_of(branch or name)
        key = (tip, issue)
        if key in self._proofs:
            return self._proofs[key]
        self._proofs[key] = self._compute_proof(tip, issue)
        return self._proofs[key]

    def _compute_proof(self, tip: str, issue: int | None) -> str:
        if self._ancestry(tip):
            return f"landed:ancestor:{tip[:12]}"
        paths = self._changed_paths(tip)
        if self._contained(tip, paths):
            return f"landed:tree-contained:{tip[:12]}"
        proof_sha = self._patch_identity(tip, issue)
        if proof_sha:
            return f"landed:patch-identity:{proof_sha[:12]}"
        return ""
