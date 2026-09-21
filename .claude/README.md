# `.claude/` — interactive prompt intake is deliberately NOT wired to the orchestrator

This directory exists so that the *absence* of prompt routing in
`agent-orchestrator` reads as a decision rather than an oversight.

- **`settings.json`** declares the decision in JSON, under
  `_orchestrator_prompt_intake`, and declares `"hooks": {}` — **no project-scope
  hook of any kind**. It is inert by construction: it changes no session.
- **The authority** is
  [`docs/decision-records/ADR-0034-interactive-prompt-intake-non-routing.md`](../docs/decision-records/ADR-0034-interactive-prompt-intake-non-routing.md).
- **The gate** is `scripts/check-prompt-intake-declaration.sh`: it refuses the
  declaration going missing, the authority dangling, or a project hook being
  added without this decision being revisited.

## The decision

An interactive Claude Code prompt typed in this repository reaches the model
**directly**. It does not pass through `fleet/brain.py`, `governance/dispatch/`,
or `gateway/proxy/`. That is intentional, not missing wiring (ADR-0034).

Autonomous work still goes through the control plane exactly as it does today —
but as an **order** or a **claim**, which are structured, typed inputs, not as
free-form prompt text.

## Why — the four measurements

1. **The intake path has no prompt-shaped entrypoint.** Every verb in
   `governance/dispatch/cli.py` takes an issue *number*:
   `claim.add_argument("--issue", type=int, required=True)`
   (`governance/dispatch/cli.py:891`; likewise `eligible` `:885`, `dispatch`
   `:924`, `release` `:934`). `fleet/brain.py:14` watches `.fleet/brain/inbox`
   for the principal's **orders**, and its CLI is `run [--watch-timeout]
   [--once]` (`fleet/brain.py:1689-1694`). Routing a prompt would mean building
   a prompt→order adaptor — new dispatch logic, which issue #1516 explicitly
   does not ask for.

2. **The one candidate router is test-only by its own docstring.**
   `gateway/proxy/wiring.py:5` "for offline demos and integration tests" and
   `:13` "Everything here stays OFFLINE". Making it load-bearing for every
   prompt would promote a harness to a production dependency.

3. **A project `UserPromptSubmit` hook would fire fleet-wide, and hooks can
   block.** Claude Code loads project-scope hooks — measured on this box
   (v2.1.278) by planting a `SessionStart` hook and a `UserPromptSubmit` hook in
   a project `.claude/settings.json` and seeing both fire on one real prompt
   (see `## Verification`). Every lane worktree is a checkout of this
   repository, so this file is present in **all** of them: one added hook would
   run on every prompt of every session on the box. The repository's own hook
   contract already names the hazard — `fleet/hooks/claude-beat.sh:89`:
   *"Never 2: in Claude's hook contract exit 2 blocks the session."* A
   fail-open routing hook is decorative; a fail-closed one bricks the fleet.
   Both are worse than an honest declaration.

4. **The hook plane already exists and is owned elsewhere.** This repository's
   own hook set is exactly `{SessionStart, PostToolUse, SubagentStop}` —
   asserted by `fleet/tests/test_runtime_beats.py:147` — and it is installed in
   the **user's** file, never a project one: `fleet/README.md:1016` ("The
   install is one line: the value of `"hooks"` in `~/.claude/settings.json`")
   and `fleet/README.md:1012` ("The fleet **provides** the hook; it never edits
   anyone's live settings"). Nothing in this repository names
   `UserPromptSubmit` at all. The live prompt-intake hook on this box is the
   operator's own, at user scope.

## Reversing this

This is a decision, so reversing it is a decision too: supersede ADR-0034 with a
new ADR, then add the hook. The gate deliberately fails the moment a project
hook appears while ADR-0034 is still `accepted`, so the two cannot drift.
