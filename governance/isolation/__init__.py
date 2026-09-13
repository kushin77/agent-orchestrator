"""Institutional lane isolation for the fleet (issue #263).

An agent session is a minted identity bound to a GitHub issue, running in its
own ``git worktree`` on a branch named for that issue, committing under that
session's own signature. See ``README.md`` for the contract and
``governance/isolation/cli.py`` for the entry point.
"""

from __future__ import annotations

from .identity import (
    COMMIT_TRAILER,
    IDENTITY_DOMAIN,
    REPO_SLUG_DEFAULT,
    IdentityRefused,
    SessionIdentity,
    branch_for,
    branch_issue,
    commit_trailer,
    mint,
    session_id_for,
    worktree_name_for,
)

__all__ = [
    "COMMIT_TRAILER",
    "IDENTITY_DOMAIN",
    "REPO_SLUG_DEFAULT",
    "IdentityRefused",
    "SessionIdentity",
    "branch_for",
    "branch_issue",
    "commit_trailer",
    "mint",
    "session_id_for",
    "worktree_name_for",
]
