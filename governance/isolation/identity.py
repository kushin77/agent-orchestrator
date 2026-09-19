"""Session identity — the mint that ties one agent to exactly one issue.

Every fleet agent session is an *identity*, not an anonymous process: a session
id, the issue it is bound to, the canonical branch for that issue, the worktree
that branch is checked out in, and the git signature every commit it makes must
carry. Isolation becomes a state the machine establishes and re-checks, instead
of a convention an agent was asked to honour.

The mint is **deterministic**: the same ``(issue, agent, lane, suffix)`` yields
the same identity, so re-dispatching a lane reattaches to the worktree it
already owns rather than forking a second one. One issue = one lane = one branch
= one signature, enforced by construction rather than by discipline.

The signature is deliberately **non-human**: ``agent-<id>`` over the reserved
``.invalid`` TLD (RFC 2606), which can never resolve and can never be mistaken
for a person. A commit's authorship therefore names the agent that produced it,
and its branch names the ticket that ordered it.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from . import policy as _policy

#: The declared control policy this module reads its thresholds from
#: (``controls.yaml``), loaded once at import time. Every "constant" below is
#: sourced from it rather than hard-coded, so ``governance/isolation/tests/
#: test_controls.py`` can mutate a value in a temporary copy of the file and
#: prove this module's behaviour actually changes (issue #885, no-false-green).
_CONTROLS = _policy.load()

#: The repo every generated "Refs" trailer points at, unless overridden.
REPO_SLUG_DEFAULT = _CONTROLS.repo_slug_default

#: Canonical branch prefix. The branch is *named after the issue id*.
BRANCH_PREFIX = _CONTROLS.branch_prefix

#: Worktree directory prefix (see ``worktree_name_for``).
WORKTREE_PREFIX = _CONTROLS.worktree_prefix

#: Reserved TLD (RFC 2606) — a signature that can never be a human address.
IDENTITY_DOMAIN = _CONTROLS.identity_domain

#: The four variables that make git sign as this session — and that therefore must
#: never be exported into a shell shared with another lane (issue #934). Measured
#: with git 2.53.0: they outrank BOTH `git config user.email` and a per-commit
#: `git -c user.email=`, so an ambient pair does not merely win by default — it
#: cannot be overridden from the command line. In a shared shell it authors every
#: commit made in it as whatever session exported it last, which is why
#: :meth:`SessionIdentity.shared_shell_env` withholds them and
#: :meth:`SessionIdentity.commit_form` prefixes them onto a single command instead.
GIT_IDENTITY_VARS = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)

#: Trailer that ties a commit back to its ticket (GR-2).
COMMIT_TRAILER = _CONTROLS.commit_trailer_template

#: A branch must encode an issue: ``issue-<n>`` or ``issue-<n>-<suffix>``.
BRANCH_RE = re.compile(r"^" + re.escape(BRANCH_PREFIX) + r"(\d+)(?:-([a-z0-9][a-z0-9-]*))?$")

#: Ids that go into branch names, paths and git config: no shell metacharacters.
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

SESSION_ID_LEN = _CONTROLS.session_id_len


class IdentityRefused(ValueError):
    """The mint inputs cannot produce a safe identity."""


def _check_agent(agent_id: str) -> str:
    """An agent id is a slug, and never an email address.

    Refusing ``@`` is the point of the rule rather than a technicality: the
    signature exists so that generated commits are attributable to an agent and
    not to whoever's laptop happened to run the loop.
    """
    if "@" in agent_id:
        raise IdentityRefused(f"agent id {agent_id!r} is an email address; agent ids are slugs, not people")
    if not AGENT_RE.match(agent_id):
        raise IdentityRefused(f"agent id {agent_id!r} is not a safe slug (expected {AGENT_RE.pattern})")
    return agent_id


def _check_suffix(suffix: str) -> str:
    if suffix and not re.match(r"^[a-z0-9][a-z0-9-]{0,31}$", suffix):
        raise IdentityRefused(f"branch suffix {suffix!r} is not a safe slug")
    return suffix


def _check_issue(issue: int) -> int:
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise IdentityRefused(f"issue {issue!r} is not a positive integer")
    return issue


def branch_for(issue: int, suffix: str = "") -> str:
    """The canonical branch for an issue: ``issue-<n>`` (plus an optional suffix).

    The suffix disambiguates a second *concurrent* session on the same issue,
    which is already a governance violation — it exists so that a collision is
    visible in the branch name rather than silently shared.
    """
    _check_issue(issue)
    suffix = _check_suffix(suffix)
    return f"{BRANCH_PREFIX}{issue}" + (f"-{suffix}" if suffix else "")


def branch_issue(branch: str) -> int | None:
    """The issue a branch encodes, or None when it encodes none."""
    match = BRANCH_RE.match(branch)
    return int(match.group(1)) if match else None


def session_id_for(issue: int, agent_id: str, lane: str, suffix: str = "") -> str:
    """Deterministic session id over the mint inputs."""
    _check_issue(issue)
    _check_agent(agent_id)
    material = "\x1f".join((str(issue), agent_id, lane or "", _check_suffix(suffix)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:SESSION_ID_LEN]


def worktree_name_for(issue: int, session_id: str) -> str:
    """The lane directory name: ``ao-<issue>-<session prefix>``."""
    return f"{WORKTREE_PREFIX}-{_check_issue(issue)}-{session_id[:8]}"


def commit_trailer(issue: int, slug: str = REPO_SLUG_DEFAULT) -> str:
    return COMMIT_TRAILER.format(slug=slug, issue=_check_issue(issue))


@dataclass(frozen=True)
class SessionIdentity:
    """One agent session, bound to one issue and one worktree."""

    session_id: str
    issue: int
    agent_id: str
    lane: str
    branch: str
    worktree: Path
    repo_slug: str = REPO_SLUG_DEFAULT
    #: When ``open`` minted this record (UTC, ``YYYY-MM-DDTHH:MM:SSZ``), or ``""``
    #: for a record written before the session plane existed (#917). The value
    #: is what tells a session-aware rule that this lane OWES a heartbeat: a
    #: legacy record owes none and is classified by the reconcile sweep instead,
    #: so 33 pre-existing records (measured 2026-09-18) do not turn red at once.
    opened_at: str = ""

    @property
    def author_name(self) -> str:
        """The git author/committer name every commit in this lane carries."""
        return f"agent-{self.agent_id}"

    @property
    def author_email(self) -> str:
        """A reserved-TLD address that can never resolve to a person."""
        return f"agent+{self.agent_id}@{IDENTITY_DOMAIN}"

    def git_signature(self) -> dict[str, str]:
        """The pair git must sign as, as DATA — never as exported variables.

        Deliberately not keyed ``GIT_AUTHOR_*``: a mapping with those keys invites
        ``export``, and exporting them is the defect this shape exists to make
        hard (issue #934). Apply them to one commit with :meth:`commit_form`.
        """
        return {"name": self.author_name, "email": self.author_email}

    @property
    def trailer(self) -> str:
        return commit_trailer(self.issue, self.repo_slug)

    def env(self) -> dict[str, str]:
        """The complete environment for a process this session owns.

        Both halves matter: ``AO_*`` tells the agent *who it is* (so its tooling
        can be audited), and ``GIT_*`` makes git sign correctly **even if the
        worktree config is lost**, so a correct signature does not depend on one
        mechanism staying intact.

        What this must not be used for is a shell shared with another lane: the
        ``GIT_*`` half outranks every config-based identity, so exporting it there
        authors the *next* commit in that shell — whichever lane makes it — as this
        session (issue #934). Use :meth:`shared_shell_env` and
        :meth:`commit_form` for that case.
        """
        return {
            "AO_SESSION_ID": self.session_id,
            "AO_ISSUE": str(self.issue),
            "AO_AGENT_ID": self.agent_id,
            "AO_LANE": self.lane,
            "AO_BRANCH": self.branch,
            "AO_WORKTREE": str(self.worktree),
            "AO_REPO_SLUG": self.repo_slug,
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_COMMITTER_NAME": self.author_name,
            "GIT_COMMITTER_EMAIL": self.author_email,
        }

    def shared_shell_env(self) -> dict[str, str]:
        """The identity as environment, minus the four variables that outrank config.

        Safe to export in a shell several lanes share: it names the session without
        arming a signature that would leak onto whoever commits next.
        """
        return {
            name: value for name, value in self.env().items() if name not in GIT_IDENTITY_VARS
        }

    def commit_form(self, message_file: str = "<message-file>") -> str:
        """The command that signs one commit as this session in a shared shell.

        The signature is PREFIXED onto the commit rather than exported, because
        that is the only form an ambient value cannot defeat: the assignment is
        scoped to this one process, so it replaces whatever the shell was carrying
        instead of being overridden by it. Committing with a bare ``git commit`` is
        equally correct — ``git config --worktree`` already holds this signature —
        but only while the shell carries no *other* session's pair.
        """
        signature = " ".join(
            f"{name}={self.author_name if name.endswith('_NAME') else self.author_email}"
            for name in GIT_IDENTITY_VARS
        )
        return f"{signature} git commit -F {message_file}"

    def shell_env(self) -> str:
        """``export`` lines, for ``eval "$(… session env)"`` in a shell."""
        return "\n".join(f"export {name}={shlex.quote(value)}" for name, value in sorted(self.env().items()))

    def to_json(self) -> dict:
        payload = {
            "session_id": self.session_id,
            "issue": self.issue,
            "agent_id": self.agent_id,
            "lane": self.lane,
            "branch": self.branch,
            "worktree": str(self.worktree),
            "repo_slug": self.repo_slug,
            "author_name": self.author_name,
            "author_email": self.author_email,
        }
        if self.opened_at:
            payload["opened_at"] = self.opened_at
        return payload

    @classmethod
    def from_json(cls, payload: dict) -> "SessionIdentity":
        # Every field added after the first records were written is READ with a
        # default: a record that predates it is still a lane record, and an
        # audit that could not read it would be blind, not strict.
        return cls(
            session_id=str(payload["session_id"]),
            issue=int(payload["issue"]),
            agent_id=str(payload["agent_id"]),
            lane=str(payload.get("lane", "")),
            branch=str(payload["branch"]),
            worktree=Path(str(payload["worktree"])),
            repo_slug=str(payload.get("repo_slug", REPO_SLUG_DEFAULT)),
            opened_at=str(payload.get("opened_at") or ""),
        )


def mint(
    issue: int,
    agent_id: str,
    lane: str = "",
    suffix: str = "",
    worktree_root: Path | str = Path.home() / "ao-worktrees",
    repo_slug: str | None = None,
) -> SessionIdentity:
    """Mint the identity for one agent working one issue.

    Deterministic: minting twice for the same inputs returns an equal identity,
    so a re-dispatch attaches to the lane that already exists. ``repo_slug``
    defaults to the *current* module-level ``REPO_SLUG_DEFAULT`` (read fresh,
    not bound at import time), so a caller that reloads the declared control
    policy sees the mint follow it.
    """
    if repo_slug is None:
        repo_slug = REPO_SLUG_DEFAULT
    _check_issue(issue)
    _check_agent(agent_id)
    session_id = session_id_for(issue, agent_id, lane, suffix)
    worktree = Path(worktree_root) / worktree_name_for(issue, session_id)
    return SessionIdentity(
        session_id=session_id,
        issue=issue,
        agent_id=agent_id,
        lane=lane,
        branch=branch_for(issue, suffix),
        worktree=worktree,
        repo_slug=repo_slug,
    )
