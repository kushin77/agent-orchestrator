"""Lane audit — the isolation contract, re-checked against the real worktree.

Provisioning establishes isolation once; this module re-derives it from the
filesystem and git every time it is asked. That is the difference between a
convention (someone was told to work in a worktree) and an institution (the
machine can prove it or refuse).

Three properties are checked, and each one is a real failure when broken:

* **the branch names the issue** — a lane whose branch does not encode the
  ticket is not linked to its issue at all;
* **the signature is the session's, and is lane-local** — a worktree whose
  identity is inherited, or whose signature landed in the shared config, would
  sign other lanes' commits as this session;
* **every commit this session authored carries the ticket trailer** — this is
  what ties generated history back to the issue, and it is checked per commit
  rather than per branch, because a branch that *mostly* references its ticket
  is exactly the drift the rule exists to stop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .identity import SessionIdentity, branch_issue
from .worktree import (
    git,
    is_linked_worktree,
    main_repo_root,
    read_stamped_identity,
    shared_identity,
)

FIELD = "\x1f"
RECORD = "\x1e"

#: A git trailer line (``Token: value``, e.g. ``Co-authored-by: …``).
_TRAILER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")

#: The ticket trailer this repo uses — ``Refs <owner>/<repo>#<issue>``. It has
#: no ``Token: value`` colon, so ``git interpret-trailers`` never classifies it.
_TRAILER_RE = re.compile(r"^Refs\s+([^\s#]+)#(\d+)$")


def trailing_ref(message: str) -> tuple[str, int] | None:
    """The ``Refs <slug>#<issue>`` line in the message's trailer region, if any.

    Walk up from the end, skipping blank lines and ``Token: value`` trailers
    (``Co-authored-by: …``); the first content line must BE the ref. A ref woven
    into a subject line or a body sentence is a mention, not a trailer, and is
    not in the trailer region (issue #287).
    """
    for line in reversed(message.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if _TRAILER_LINE_RE.match(stripped):
            continue
        match = _TRAILER_RE.match(stripped)
        return (match.group(1), int(match.group(2))) if match else None
    return None


def commit_carries_trailer(message: str, trailer: str) -> bool:
    """True when ``trailer`` stands alone in the message's trailer region."""
    ref = trailing_ref(message)
    return ref is not None and f"Refs {ref[0]}#{ref[1]}" == trailer


@dataclass(frozen=True)
class Violation:
    """One broken isolation property, named so it can be quoted as evidence."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _current_branch(worktree: Path) -> str:
    head = git(worktree, "symbolic-ref", "--short", "-q", "HEAD")
    return head.stdout.strip() if head.returncode == 0 else ""


def authored_commits(worktree: Path, email: str) -> list[tuple[str, str]]:
    """Every commit in this lane authored by ``email``, as (sha, message).

    Selection is by *author identity*, not by branch range: the rule is that
    this session's generated history points at this session's ticket, and that
    holds whatever the branch was based on (and however often it was rebased).
    """
    log = git(worktree, "log", f"--format=%H{FIELD}%ae{FIELD}%B{RECORD}", "HEAD")
    if log.returncode != 0:
        return []
    commits = []
    for chunk in log.stdout.split(RECORD):
        if not chunk.strip():
            continue
        parts = chunk.strip("\n").split(FIELD, 2)
        if len(parts) != 3:
            continue
        sha, author_email, message = parts
        if author_email.strip() == email:
            commits.append((sha.strip(), message))
    return commits


def audit_lane(identity: SessionIdentity, main: Path | str) -> list[Violation]:
    """Every isolation property this lane breaks (empty list = isolated)."""
    problems: list[Violation] = []
    worktree = identity.worktree

    encoded = branch_issue(identity.branch)
    if encoded != identity.issue:
        problems.append(
            Violation(
                "branch-does-not-name-issue",
                f"branch {identity.branch!r} does not encode issue #{identity.issue}",
            )
        )

    if not worktree.exists():
        problems.append(Violation("worktree-missing", f"{worktree} does not exist"))
        return problems
    if not is_linked_worktree(worktree):
        problems.append(Violation("worktree-not-linked", f"{worktree} is not a linked git worktree"))
        return problems

    current = _current_branch(worktree)
    if current != identity.branch:
        problems.append(
            Violation(
                "branch-mismatch",
                f"{worktree} is on {current or 'a detached HEAD'}, not the lane branch {identity.branch!r}",
            )
        )

    stamped = read_stamped_identity(worktree)
    if stamped is None:
        problems.append(
            Violation(
                "identity-not-lane-local",
                f"{worktree} carries no worktree-scoped signature; its commits would inherit another identity",
            )
        )
    elif stamped != (identity.author_name, identity.author_email):
        problems.append(
            Violation(
                "identity-mismatch",
                f"{worktree} signs as {stamped[0]} <{stamped[1]}>, not {identity.author_name} <{identity.author_email}>",
            )
        )

    shared = shared_identity(main_repo_root(main))
    if shared is not None and shared[1] == identity.author_email:
        problems.append(
            Violation(
                "identity-leaked-to-shared-config",
                f"the shared repository config signs as {identity.author_email}; every lane would inherit it",
            )
        )

    missing = [
        sha
        for sha, message in authored_commits(worktree, identity.author_email)
        if not commit_carries_trailer(message, identity.trailer)
    ]
    if missing:
        shown = ", ".join(sha[:8] for sha in missing[:5])
        problems.append(
            Violation(
                "commit-missing-ticket-trailer",
                f"{len(missing)} commit(s) authored by {identity.author_email} omit {identity.trailer!r}: {shown}",
            )
        )
    return problems


def audit_all(main: Path | str) -> dict[str, list[Violation]]:
    """Audit every recorded lane; a lane id maps to its violations."""
    from .worktree import list_records

    return {identity.session_id: audit_lane(identity, main) for identity in list_records(main)}
