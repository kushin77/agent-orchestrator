---
description: "Use when: about to run git commit or git push, staging files, or preparing a commit on a branch. Renders CMR's pre-commit self-check (branch is not main; staged files match the current lane) — required before every commit, not just the first."
applyTo: "**"
---

# Pre-commit self-check — CMR

> Copilot-discoverable rendering of `guardrails/instructions/pre-commit-self-check.md`
> (canonical, referenced from `AGENTS.md` → Verification). Binding before **every**
> `git commit`.

Run both commands and read the output:

```bash
git branch --show-current    # must NOT print "main"
git status --short           # only files for the current issue/lane are staged
```

- **Branch is `main`** → stop. Create the issue's branch first
  (`docs/EXECUTION-PLAN.md` lane convention; branch shape `<lane>-<slug>`). Do not commit
  to `main` and plan to "fix it in the PR" — a commit already on `main` has no PR to open
  for it.
- **Staged files outside the current lane's `owns` glob** → stop and reconcile before
  committing. A mixed commit breaks "one issue = one lane = one branch"
  (`agent-operating-doctrine.instructions.md`) and makes GR-12 evidence harder to trace to
  the issue it proves.

This is a standing discipline: re-run it before every commit in a session, not once.

## Enforcement status

The canonical file records that mechanical enforcement (`.githooks/pre-commit` rejecting a
`main` commit) is **not wired** and is tracked as board work — so this check stays
agent-run rather than gate-run. Do not assume the hook caught it.
