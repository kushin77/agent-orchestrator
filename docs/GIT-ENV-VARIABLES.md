# Git/Env Variable Registry

Canonical registry of the session env contract exported by
[`governance/isolation/**`](../governance/isolation/) and named in
[`AGENTS.md`](../AGENTS.md) golden rule 15. This closes the gap measured by
issue #608 (parent EPIC #616): the contract lived only in prose, with no
single table a lane or a checker could read. It is that table.

Every variable below is emitted by
[`SessionIdentity.env()`](../governance/isolation/identity.py) (and its two
narrower views, `shared_shell_env()` and `commit_form()`) — there is exactly
one producer for the whole set.

## `AO_*` — session identity

Tells the agent, and anything auditing it, who it is. Safe to export into a
shell shared with another lane (`shared_shell_env()` keeps all seven).

| Variable | Purpose | Producer | Consumer | Rule |
|---|---|---|---|---|
| `AO_SESSION_ID` | Deterministic session id, minted from `(issue, agent, lane, suffix)` — re-dispatch reattaches to the same lane instead of forking a new one. | `governance/isolation/identity.py` (`SessionIdentity.env`, `session_id_for`) | dispatch/isolation (lane identity), asserted in `governance/isolation/tests/test_identity.py` | AGENTS.md GR-15 |
| `AO_ISSUE` | The issue this session is bound to. | `governance/isolation/identity.py` | dispatch/lifecycle (one-issue-one-lane enforcement) | AGENTS.md GR-15 |
| `AO_AGENT_ID` | Slug identifying the agent (never an email address — `identity.py`'s `_check_agent` refuses `@`). | `governance/isolation/identity.py` | dispatch (session authorship), lane naming (`worktree_name_for`) | AGENTS.md GR-15 |
| `AO_LANE` | The lane label the session was minted under. | `governance/isolation/identity.py` | dispatch (lane routing) | AGENTS.md GR-15 |
| `AO_BRANCH` | The canonical branch for the issue (`issue-<n>` or `issue-<n>-<suffix>`). | `governance/isolation/identity.py` (`branch_for`) | dispatch/isolation (worktree checkout, branch verification) | AGENTS.md GR-15 |
| `AO_WORKTREE` | Absolute path of the worktree this session owns. | `governance/isolation/identity.py` (`worktree_name_for`, `mint`) | isolation (path guards), lifecycle (cleanup/reclaim) | AGENTS.md GR-15 |
| `AO_REPO_SLUG` | `org/repo` slug the generated `Refs` commit trailer points at (default `kushin77/agent-orchestrator`). | `governance/isolation/identity.py` (`REPO_SLUG_DEFAULT`, `commit_trailer`) | commit-message trailer generation | AGENTS.md GR-15 |
| `AO_WORKTREE_ROOT` | Root directory lanes live under, read from the environment with a fallback to `~/ao-worktrees`. Not part of `SessionIdentity.env()` — it is an *input* the caller may set before minting, not an output the mint produces. | read by `governance/isolation/worktree.py` (`worktree_root`) | isolation (`mint`'s default `worktree_root`) | AGENTS.md GR-15 |

## `GIT_AUTHOR_*` / `GIT_COMMITTER_*` — commit signature

The four variables that make git sign a commit as this session, listed in
`governance/isolation/identity.py` as `GIT_IDENTITY_VARS`. They deliberately
outrank both `git config user.email` and a per-commit `git -c user.email=`
(measured with git 2.53.0), which is why they must **never** be exported into
a shell shared with another lane (issue #934) — an ambient pair there would
author whichever lane commits next as this session. `shared_shell_env()`
withholds all four for exactly this reason; `commit_form()` prefixes them
onto a single `git commit` command instead of exporting them.

| Variable | Purpose | Producer | Consumer | Rule |
|---|---|---|---|---|
| `GIT_AUTHOR_NAME` | `agent-<agent_id>` — the non-human name every commit in this lane carries. | `governance/isolation/identity.py` (`SessionIdentity.author_name`) | `commit_form()` (prefixed, one commit); refused as an export target by `shared_shell_env()` | AGENTS.md GR-15; issue #934 |
| `GIT_AUTHOR_EMAIL` | `agent+<agent_id>@agents.invalid` — a reserved-TLD (RFC 2606) address that can never resolve to a person. | `governance/isolation/identity.py` (`SessionIdentity.author_email`, `IDENTITY_DOMAIN`) | `commit_form()`; refused as an export target by `shared_shell_env()` | AGENTS.md GR-15; issue #934 |
| `GIT_COMMITTER_NAME` | Same value as `GIT_AUTHOR_NAME` — author and committer are always the same agent. | `governance/isolation/identity.py` | `commit_form()`; refused as an export target by `shared_shell_env()` | AGENTS.md GR-15; issue #934 |
| `GIT_COMMITTER_EMAIL` | Same value as `GIT_AUTHOR_EMAIL`. | `governance/isolation/identity.py` | `commit_form()`; refused as an export target by `shared_shell_env()` | AGENTS.md GR-15; issue #934 |

## The drift contract

Adding, renaming, or removing one of these variables is **one change**, not
two: the exporter (`governance/isolation/identity.py`, principally
`SessionIdentity.env`) and this registry are updated **together**, in the
same commit.

Today nothing mechanically enforces that pairing. Two things stand near it
but do not close the loop:

- [`governance/isolation/tests/test_identity.py`](../governance/isolation/tests/test_identity.py)
  and
  [`governance/isolation/tests/test_authorship.py`](../governance/isolation/tests/test_authorship.py)
  assert on the literal `AO_*`/`GIT_*` keys `env()` emits — a *renamed or
  dropped* key fails those tests first. But a lane can *add* a new key to
  `env()`, add a matching test assertion, and never touch this file: nothing
  goes red for that case.
- `bash scripts/check-docs.sh` (this doc's own verify command) checks that
  `docs/*.md` files satisfy a fixed foundation-file list and pass link/
  whitespace/marker rules — it does not check that this registry stays in
  sync with `identity.py`.

The check that closes that gap is issue **#630** ("Wire the new gates into
`make verify`"), the wiring child of EPIC #616, which is `Blocked-by` two of
its sibling lanes (#621, #624, #625). Until #630 lands, keeping the exporter
and this registry in sync is a reviewer discipline, not a gate. AGENTS.md
golden rule 15 names the eight-plus-four variable set this registry tables;
this file is where "which file sets X, which file reads X" is looked up
instead of re-derived from prose.

## Related

- [`AGENTS.md`](../AGENTS.md) — golden rule 15 (session env contract).
- [`docs/GIT-TEMPLATES-GAP-ANALYSIS.md`](GIT-TEMPLATES-GAP-ANALYSIS.md) — the gap analysis (issue #608) that ordered this registry, under EPIC #616.
- [`governance/isolation/README.md`](../governance/isolation/README.md) — the isolation module's own operator-facing notes on `AO_*` vs `GIT_*`.
