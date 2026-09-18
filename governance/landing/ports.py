#!/usr/bin/env python3
"""The effects a landing needs — injected, with real implementations (#764).

The landing engine is a decision plus an ordered set of effects:

    push -> open PR -> pre-merge contract -> merge decision -> squash-merge
         -> delete the source branch -> lifecycle close

This module holds the *effects*: a thin, inspectable wrapper over ``git`` and
``gh`` (the auth the runner already has — the driver never handles a token), plus
a recording implementation used for the dry run.

Two properties are structural here rather than promised:

* **Nothing forces or rewrites history.** ``push`` runs ``git push -u origin
  <branch>`` and nothing else — no ``--force``, no ``--force-with-lease``, no
  refspec that could delete or overwrite a ref. A rejected push raises
  :class:`PortError` and the landing stops; it is never forced through.
* **A failed effect is an error, a red contract is a verdict.** ``push``,
  ``open_pr``, ``merge_pr`` and ``delete_branch`` raise when they fail (the
  effect did not happen). ``run_contract`` and ``close_lifecycle`` return their
  exit codes instead, because for those the exit code *is* the outcome (the
  honesty tri-state 0/1/2), and collapsing it into an exception would lose which
  of "failed" and "could not assess" it was.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, Sequence, Tuple

GH_PR_FIELDS = "number,state,mergeCommit,baseRefName,headRefOid,title,url"
PR_URL_NUMBER = re.compile(r"/(\d+)/?$")


class PortError(RuntimeError):
    """An effect could not be performed (a missing tool, a rejected push, a red gh)."""


@dataclass(frozen=True)
class CommandResult:
    """One command's outcome, with its exit code kept intact."""

    argv: Tuple[str, ...]
    rc: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def command(self) -> str:
        return " ".join(self.argv)

    def tail(self, lines: int = 30) -> str:
        """The last ``lines`` lines of whichever stream carries the output."""
        text = self.stdout if self.stdout.strip() else self.stderr
        rows = text.splitlines()
        return "\n".join(rows[-lines:]) if rows else ""


@dataclass(frozen=True)
class PullRequest:
    """The pull request as GitHub reports it (the state the driver must read)."""

    number: int
    state: str
    head: str = ""
    base: str = ""
    merge_commit: Optional[str] = None
    title: str = ""
    url: str = ""

    @property
    def merged(self) -> bool:
        return self.state.upper() == "MERGED"

    @property
    def open(self) -> bool:
        return self.state.upper() == "OPEN"

    @classmethod
    def from_gh(cls, payload: dict) -> "PullRequest":
        merge = payload.get("mergeCommit")
        merge_oid = merge.get("oid") if isinstance(merge, dict) else None
        return cls(
            number=int(payload.get("number") or 0),
            state=str(payload.get("state") or ""),
            head=str(payload.get("headRefOid") or ""),
            base=str(payload.get("baseRefName") or ""),
            merge_commit=str(merge_oid) if merge_oid else None,
            title=str(payload.get("title") or ""),
            url=str(payload.get("url") or ""),
        )


class LandingOps(Protocol):
    """Every effect (and the reads they depend on) a landing performs."""

    def head_commit(self) -> str:
        """The lane head commit — the commit an attestation must name."""

    def latest_subject(self, rev: str) -> str:
        """The subject line of ``rev`` (the PR title when none is given)."""

    def commit_subjects(self, base: str, rev: str) -> Tuple[str, ...]:
        """The subjects of the lane's own commits (the PR's what-changed list)."""

    def changed_files(self, base: str, rev: str) -> Tuple[str, ...]:
        """The files the lane's diff touches (``git diff --name-only base...rev``).

        Used to MEASURE the ``Gate-changing:`` declaration in the composed PR
        body against ``scripts/lib/gate-paths.txt`` — never hard-coded.
        """

    def remote_branch_head(self, branch: str) -> Optional[str]:
        """The remote head of ``branch``, or None when the branch is not pushed."""

    def merge_base(self, left: str, right: str) -> Optional[str]:
        """The merge base of ``left`` and ``right``, or None when there is none.

        Used ONLY to decide whether publishing master's health after a merge
        is honest (fix #5 follow-up, #1114): a lane's own attestation
        measured the LANE head, and relabelling that as a measurement of
        master's post-squash head is only fair when the lane head already
        contained master's pre-merge tip — i.e. ``merge_base(lane_head,
        master_head) == master_head``. Never used for anything else.
        """

    def pull_request_for(self, branch: str) -> Optional[PullRequest]:
        """The pull request whose head is ``branch`` (any state), or None."""

    def push(self, branch: str) -> str:
        """Publish the lane branch (never a force-push)."""

    def open_pr(self, *, branch: str, base: str, title: str, body_file: Path) -> PullRequest:
        """Open the pull request for the lane branch."""

    def run_contract(self, *, pr_number: Optional[int]) -> CommandResult:
        """Run the pre-merge contract (``scripts/merge-gate.sh run``)."""

    def publish_status(self, *, sha: str, rc: int) -> CommandResult:
        """Publish the gate of record as a GitHub commit status (ADR-0028).

        ``rc`` is the pre-merge contract's own normalised tri-state (0/1/2),
        never a subprocess return code passed through unexamined. The exit
        code of the returned :class:`CommandResult` is the *poster's*
        outcome: 0 means the status was posted (and read back), anything else
        means it was not — a failed or unreadable poster, never a guess.
        """

    def check_landed_contract(self, *, base: str, head: str) -> CommandResult:
        """Run the landed-contract trailer precondition over the commits to be squashed."""

    def merge_pr(self, number: int, *, subject: str, body_file: Path) -> str:
        """Squash-merge the pull request with an explicit trailer-bearing message; return the merge commit."""

    def delete_branch(self, branch: str) -> str:
        """Delete the remote source branch."""

    def close_lifecycle(self, issue: int) -> CommandResult:
        """Drive hygienic closure (``governance/lifecycle/cli.py close``)."""


def _run(argv: Sequence[str], *, cwd: Optional[Path] = None, env: Optional[dict] = None) -> CommandResult:
    try:
        proc = subprocess.run(
            list(argv), cwd=str(cwd) if cwd else None, env=env,
            capture_output=True, text=True,
        )
    except FileNotFoundError as exc:
        raise PortError(f"{argv[0]} is not installed ({exc})") from exc
    return CommandResult(
        argv=tuple(argv), rc=proc.returncode,
        stdout=proc.stdout or "", stderr=proc.stderr or "",
    )


class GitHubOps:
    """The real effects: ``git`` and ``gh`` in an explicit working tree.

    ``gh`` is used as the runner's own authenticated client — no token is read,
    written, or passed anywhere by this driver.
    """

    def __init__(self, root: Path, *, base: str = "master", env: Optional[dict] = None) -> None:
        self.root = Path(root)
        self.base = base
        self.env = env

    # -- reads -----------------------------------------------------------------

    def _git(self, *args: str) -> CommandResult:
        return _run(["git", "-C", str(self.root), *args], env=self.env)

    def _gh(self, *args: str) -> CommandResult:
        return _run(["gh", *args], cwd=self.root, env=self.env)

    def _git_text(self, *args: str) -> str:
        result = self._git(*args)
        if not result.ok:
            raise PortError(f"`git {' '.join(args)}` failed (rc={result.rc}): {result.stderr.strip()[:200]}")
        return result.stdout.strip()

    def head_commit(self) -> str:
        return self._git_text("rev-parse", "HEAD")

    def latest_subject(self, rev: str) -> str:
        return self._git_text("log", "-1", "--format=%s", rev)

    def commit_subjects(self, base: str, rev: str) -> Tuple[str, ...]:
        out = self._git_text("log", "--no-merges", "--format=%s", f"{base}..{rev}")
        return tuple(line for line in out.splitlines() if line.strip())

    def changed_files(self, base: str, rev: str) -> Tuple[str, ...]:
        out = self._git_text("diff", "--name-only", f"{base}...{rev}")
        return tuple(line for line in out.splitlines() if line.strip())

    def remote_branch_head(self, branch: str) -> Optional[str]:
        out = self._git_text("ls-remote", "--heads", "origin", branch)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].endswith(f"/{branch}"):
                return parts[0]
        return None

    def merge_base(self, left: str, right: str) -> Optional[str]:
        result = self._git("merge-base", left, right)
        if not result.ok:
            # No common ancestor (or either name is unresolvable in this
            # checkout) — an honest "cannot tell", not an exception. The
            # caller (the master-attestation guard) treats this as "not
            # already at master", the safe default.
            return None
        return result.stdout.strip() or None

    def pull_request_for(self, branch: str) -> Optional[PullRequest]:
        result = self._gh(
            "pr", "list", "--head", branch, "--state", "all", "--limit", "5",
            "--json", GH_PR_FIELDS,
        )
        if not result.ok:
            raise PortError(f"`gh pr list --head {branch}` failed (rc={result.rc}): {result.stderr.strip()[:200]}")
        try:
            payload = json.loads(result.stdout or "[]")
        except ValueError as exc:
            raise PortError(f"`gh pr list --head {branch}` returned unparseable JSON: {exc}") from exc
        if not isinstance(payload, list) or not payload:
            return None
        return PullRequest.from_gh(payload[0])

    # -- effects ---------------------------------------------------------------

    def push(self, branch: str) -> str:
        """Publish the branch. Never ``--force``, never a history rewrite."""
        result = self._git("push", "-u", "origin", branch)
        if not result.ok:
            raise PortError(
                f"`git push -u origin {branch}` was refused (rc={result.rc}): "
                f"{result.stderr.strip()[-300:]} — the driver never force-pushes"
            )
        return f"git -C {self.root} push -u origin {branch}"

    def open_pr(self, *, branch: str, base: str, title: str, body_file: Path) -> PullRequest:
        result = self._gh(
            "pr", "create", "--base", base, "--head", branch,
            "--title", title, "--body-file", str(body_file),
        )
        if not result.ok:
            raise PortError(f"`gh pr create` failed (rc={result.rc}): {result.stderr.strip()[-300:]}")
        opened = self.pull_request_for(branch)
        if opened is None:
            raise PortError(f"`gh pr create` reported success but no pull request exists for {branch}")
        return opened

    def run_contract(self, *, pr_number: Optional[int]) -> CommandResult:
        """The executable pre-merge contract, at the PR boundary (issue #29).

        ``AO_PR_NUMBER`` is *added* to the environment the runner already has
        (PATH for git/gh, HOME, the session's GIT_* identity) — never used to
        replace it: an empty environment would run the contract without git on
        PATH and the failure would look like a red gate.

        The argument is authoritative in both directions: with no pull request the
        variable is *removed*, not merely left unset. The driver runs the contract
        with ``AO_PR_NUMBER`` set, and the contract's own ``tests`` signal runs
        the suite sweep inside that environment — so an inherited value reaches
        the corpus and mislabels a no-PR contract run as having one. That is
        exactly what
        ``test_without_a_pull_request_the_contract_runs_without_the_pr_context``
        catches, and how this was found (#764).
        """
        env = dict(self.env) if self.env else dict(os.environ)
        env.pop("AO_PR_NUMBER", None)
        if pr_number:
            env["AO_PR_NUMBER"] = str(pr_number)
        return _run(["bash", str(self.root / "scripts" / "merge-gate.sh"), "run"], cwd=self.root, env=env)

    def publish_status(self, *, sha: str, rc: int) -> CommandResult:
        """``bash scripts/gate-status.sh post --sha <sha> --rc <rc>`` (ADR-0028, #1072).

        Run from the repo root, exactly as the poster's own header documents.
        The mapping from a gate outcome to a commit-status state lives ONLY in
        ``scripts/gate-status-map.py`` — this port does not re-decide it, it
        just runs the poster and reports what the poster reported.
        """
        return _run(
            ["bash", str(self.root / "scripts" / "gate-status.sh"), "post", "--sha", sha, "--rc", str(rc)],
            cwd=self.root,
            env=self.env,
        )

    def check_landed_contract(self, *, base: str, head: str) -> CommandResult:
        """The merge precondition over the artifact that lands (issue #998).

        The repository sets ``squash_merge_commit_message=COMMIT_MESSAGES``, so
        the landed commit body is composed from the BRANCH COMMIT MESSAGES, not
        the PR body: a PR whose body is contract-perfect still lands trailer-less
        if its commit message lacks the ticket trailer. This runs the shared
        predicate's landed audit over exactly the commits that will be squashed
        — ``scripts/check-pr-contract.sh --landed --range <base>..<head>`` — the
        same parser the landed-history audit (``check-isolation-landed``) runs,
        moved to *before* the merge rather than after it. One rule, one parser,
        enforced on the artifact that lands.
        """
        return _run(
            [
                "bash",
                str(self.root / "scripts" / "check-pr-contract.sh"),
                "--landed",
                "--range",
                f"{base}..{head}",
            ],
            cwd=self.root,
            env=self.env,
        )

    def merge_pr(self, number: int, *, subject: str, body_file: Path) -> str:
        """Squash-merge with an explicit trailer-bearing message; the branch delete stays its own step.

        The explicit ``--subject``/``--body-file`` makes the landed artifact
        deterministic: instead of relying on GitHub's COMMIT_MESSAGES
        composition, the squash commit body is the one the caller composed and
        validated, so its trailing ticket trailer survives the merge (issue #998).
        """
        result = self._gh(
            "pr", "merge", str(number), "--squash",
            "--subject", subject, "--body-file", str(body_file),
        )
        if not result.ok:
            raise PortError(
                f"`gh pr merge {number} --squash --subject ... --body-file ...` failed "
                f"(rc={result.rc}): {result.stderr.strip()[-300:]}"
            )
        view = self._gh("pr", "view", str(number), "--json", "state,mergeCommit")
        if not view.ok:
            raise PortError(f"`gh pr view {number}` failed (rc={view.rc}): {view.stderr.strip()[-200:]}")
        payload = json.loads(view.stdout or "{}")
        if str(payload.get("state") or "").upper() != "MERGED":
            raise PortError(f"PR {number} is {payload.get('state')!r}, not merged")
        merge = payload.get("mergeCommit") or {}
        return str(merge.get("oid") or "")

    def delete_branch(self, branch: str) -> str:
        result = self._git("push", "origin", "--delete", branch)
        if not result.ok:
            raise PortError(f"`git push origin --delete {branch}` failed (rc={result.rc}): {result.stderr.strip()[-300:]}")
        return f"git -C {self.root} push origin --delete {branch}"

    def close_lifecycle(self, issue: int) -> CommandResult:
        """``governance/lifecycle/cli.py close`` (issue #269) — joined, not redone."""
        return _run(
            ["python3", str(self.root / "governance" / "lifecycle" / "cli.py"), "close", "--issue", str(issue)],
            cwd=self.root, env=self.env,
        )


@dataclass
class Planned:
    """A step the dry run would perform."""

    action: str
    detail: str


@dataclass
class RecordingOps:
    """Dry-run ops: reads are real, every write is recorded and NOT performed.

    The reads stay real on purpose — the plan must be a statement about the lane
    as it actually is (is the PR already merged? is the branch already pushed?),
    not about a guessed world. Only the writes are intercepted.
    """

    reads: LandingOps
    planned: list = field(default_factory=list)

    def _plan(self, action: str, detail: str) -> str:
        self.planned.append(Planned(action, detail))
        return detail

    # reads — delegated
    def head_commit(self) -> str:
        return self.reads.head_commit()

    def latest_subject(self, rev: str) -> str:
        return self.reads.latest_subject(rev)

    def commit_subjects(self, base: str, rev: str) -> Tuple[str, ...]:
        return self.reads.commit_subjects(base, rev)

    def changed_files(self, base: str, rev: str) -> Tuple[str, ...]:
        return self.reads.changed_files(base, rev)

    def remote_branch_head(self, branch: str) -> Optional[str]:
        return self.reads.remote_branch_head(branch)

    def merge_base(self, left: str, right: str) -> Optional[str]:
        return self.reads.merge_base(left, right)

    def pull_request_for(self, branch: str) -> Optional[PullRequest]:
        return self.reads.pull_request_for(branch)

    # writes — recorded only
    def push(self, branch: str) -> str:
        return self._plan("push", f"git push -u origin {branch}")

    def open_pr(self, *, branch: str, base: str, title: str, body_file: Path) -> PullRequest:
        self._plan("open-pr", f"gh pr create --base {base} --head {branch} --title {title!r} --body-file {body_file}")
        return PullRequest(number=0, state="PLANNED", head="", base=base, title=title)

    def run_contract(self, *, pr_number: Optional[int]) -> CommandResult:
        self._plan("contract", f"bash scripts/merge-gate.sh run (AO_PR_NUMBER={pr_number or 'unset'})")
        return CommandResult(argv=("bash", "scripts/merge-gate.sh", "run"), rc=0)

    def publish_status(self, *, sha: str, rc: int) -> CommandResult:
        self._plan("gate-status", f"bash scripts/gate-status.sh post --sha {sha} --rc {rc}")
        return CommandResult(argv=("bash", "scripts/gate-status.sh", "post"), rc=0)

    def check_landed_contract(self, *, base: str, head: str) -> CommandResult:
        self._plan("landed-contract", f"bash scripts/check-pr-contract.sh --landed --range {base}..{head}")
        return CommandResult(argv=("bash", "scripts/check-pr-contract.sh", "--landed"), rc=0)

    def merge_pr(self, number: int, *, subject: str, body_file: Path) -> str:
        return self._plan("merge", f"gh pr merge {number} --squash --subject {subject!r} --body-file {body_file}")

    def delete_branch(self, branch: str) -> str:
        return self._plan("delete-branch", f"git push origin --delete {branch}")

    def close_lifecycle(self, issue: int) -> CommandResult:
        self._plan("lifecycle-close", f"python3 governance/lifecycle/cli.py close --issue {issue}")
        return CommandResult(argv=("python3", "governance/lifecycle/cli.py", "close"), rc=0)
