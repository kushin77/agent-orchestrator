# governance/platform — declared platform-level controls (issue #803)

Declares the GitHub-platform-level settings that back the gate of record, as
code rather than as an undocumented operator API call. `AGENTS.md` used to
claim "`master` is protected by convention" while
`gh api repos/kushin77/agent-orchestrator/branches/master/protection` returned
404 — a rule that lives only in prose is advisory, not binding. These files are
the reproducible declaration; the matching `scripts/*.sh` apply and verify them
against the live repo.

## Layout

| File | Role |
|---|---|
| [`branch-protection.yaml`](branch-protection.yaml) | declared branch protection + required status checks for `master` (issue #803, P0-3); applied/verified by `scripts/branch-protection.sh` |
| [`repo-settings.yaml`](repo-settings.yaml) | declared squash-merge message policy (`squash_merge_commit_message=PR_BODY`) so the PR body's ticket trailer lands on the squash commit (issue #1138, parent #803); applied/verified by `scripts/repo-settings.sh` |

## Why this exists

Both settings were previously applied by a one-off operator API call — itself
a GR-5 breach (infrastructure is declared, never clicked) — and neither was
reproducible across a fork, a restore, or a second repo. These files close
that gap: the setting is declared once, applied by a named script, and the
gate reads the live setting back to confirm it still matches.

## Related

Issue #803 (platform-level delivery controls), issue #1138 (squash-merge
trailer policy), issue #6 (branch protection as code).
