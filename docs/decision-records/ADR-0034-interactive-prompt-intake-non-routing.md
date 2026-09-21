---
id: ADR-0034
status: accepted
date: 2026-09-20
deciders: [owner]
req: []
supersedes: []
---

# ADR-0034: An interactive Claude Code prompt does not route through the orchestrator — the direct path is the design

## Status

`accepted` — decided on the evidence measured in [Context](#context), under
issue #1516 (a child of the offshore epic #1510), whose acceptance explicitly
allows either a working routing hook **or** a documented non-routing decision.

**Numbering note:** the highest ADR **file** on `origin/master` at decision time
is `ADR-0032`. `0033` is already claimed twice over by open lanes
(`ADR-0033-bridge-consumer-contract.md` on the console lane, and
`ADR-0033-paperclip-hermes-naming-resolution.md` on #1514's lane), so taking it
would collide; a tree-wide grep (excluding `vendor/`, `.git/`, `.board/`) finds
zero citations of `ADR-0034`. This record therefore takes `0034`.

**Index note:** `docs/decision-records/README.md` is not edited by this change.
That file is held by three open pull requests at decision time (#1628, #1629,
#1634), and the ADR index row is a one-line addition better made by whichever of
them lands last. This gap is declared here so it reads as a deferred edit rather
than a missing one; `docs/decision-records/` is a deliberately-unindexed
directory for `docs-lint` (`scripts/check-docs.sh`, `idx_excluded_dirs`), so
nothing else depends on the row.

## Context

Issue #1516 asks a two-sided question: should an interactive Claude Code prompt
be intercepted and routed through `fleet/brain.py` / `governance/dispatch/`
before free-form tool execution — or is the direct path the right design, with
`fleet/` reserved for autonomous work? Its own note says a hook that fires and
does nothing is worse than an honest documented decision, and `#1510`'s
governance gap is exactly that *"how does a prompt reach the fleet" was never
explicitly decided either way — it defaulted to "doesn't" by absence, not by
design.*

Four measurements decide it. Each is reproducible from the repository alone.

**1. The intake path has no prompt-shaped entrypoint.** Every verb in
`governance/dispatch/cli.py` takes an issue *number*:
`claim.add_argument("--issue", type=int, required=True)`
(`governance/dispatch/cli.py:891`; `eligible` `:885`, `dispatch` `:924`,
`release` `:934`). `fleet/brain.py:14` "watches `.fleet/brain/inbox` for the
principal's **orders**", and its CLI is `run [--watch-timeout] [--once]`
(`fleet/brain.py:1689-1694`). The control plane's input vocabulary is an issue
number or a signed order — there is no code path that accepts a prompt string.

**2. The only candidate router is a test harness, by its own docstring.**
`gateway/proxy/wiring.py:5` exists "for offline demos and integration tests" and
`:13` states "Everything here stays OFFLINE". Routing every interactive prompt
through it would promote a harness to a production dependency.

**3. Project-scope hooks fire, and a hook can block a session.** Measured on
this box with Claude Code `2.1.278`: planting a `SessionStart` hook and then a
`UserPromptSubmit` hook in a project `.claude/settings.json` fired **both** on a
single real prompt (see [Verification](#verification)). Project settings are
therefore live, not inert — and every lane worktree is a checkout of this
repository, so this file lands in all of them at once. The repository's own hook
contract already names the hazard: `fleet/hooks/claude-beat.sh:89` — *"Never 2:
in Claude's hook contract exit 2 blocks the session."* A routing hook that fails
open is decorative; one that fails closed stops the fleet. For an interactive
session, neither is acceptable.

**4. The hook plane already exists and is owned at user scope.** This
repository's own hook set is exactly
`{SessionStart, PostToolUse, SubagentStop}` — asserted by
`fleet/tests/test_runtime_beats.py:147` — and it is installed in the **user's**
settings, never a project file: `fleet/README.md:1012` ("The fleet **provides**
the hook; it never edits anyone's live settings") and `fleet/README.md:1016`
("The install is one line: the value of `"hooks"` in
`~/.claude/settings.json`"). Nothing in this repository names
`UserPromptSubmit` (`git grep UserPromptSubmit -- . ':!vendor'` is empty). On
this box the live `UserPromptSubmit` hook is the operator's own
`~/.claude/settings.json` entry, which runs `machine-awareness --hook` — a
resource-governance probe, not an orchestrator router.

## Decision

**Interactive prompts are not routed. The direct path is the design.**

1. An interactive Claude Code session in this repository sends its prompt
   straight to the model. No hook, no proxy, no dispatch call sits in front of
   it.
2. `fleet/` and `governance/dispatch/` remain the intake for **autonomous**
   work, and their input stays what it already is: a board issue number
   (`--issue type=int`) or a brain-signed order in `.fleet/brain/inbox`. No
   prompt→order adaptor is introduced.
3. The decision is declared, not assumed: this repository carries a project
   `.claude/settings.json` whose `_orchestrator_prompt_intake` object names the
   decision and this record, with `"hooks": {}` — deliberately **no** project
   hook of any kind — and `.claude/README.md` explains it in prose.
4. The declaration is enforced by
   `scripts/check-prompt-intake-declaration.sh`, which refuses the declaration
   going missing, the authority dangling, the house sections being stripped, or
   a project hook appearing while this record is `accepted` (AO-GR-4, GR-29: an
   unenforced declaration is advisory).

This record does **not** forbid a project hook forever. It requires that
whichever is added arrives with the decision revisited — i.e. a superseding ADR
— rather than by drift.

## Promotion decision

The question the parent epic raises is whether Paperclip/Hermes "promotion" —
making either name the top-level orchestrator of engineering work — should
happen *by wiring a prompt hook*. The answer, and the reason this belongs in the
same record, is **no, and not here**:

- **Promotion is not a prompt-routing act.** The authoritative dispatcher is
  already `fleet/brain.py` + `governance/dispatch/` (ADR-0012; reaffirmed by
  the naming-resolution record on #1514's lane, ADR-0033). A prompt hook would
  add a *second* intake in front of an already-decided one — the duplication
  #1514 exists to correct — not promote anything.
- **Nothing is promoted by this ADR.** No flag is flipped, no endpoint is made
  live, no artifact changes authority. The only artifact added is a declaration
  that the current behaviour is intended.
- **If promotion is ever decided**, it is a dispatch-plane change (an order
  producer, a tier, a gate), and it is recorded in the dispatch/hierarchy
  records — not smuggled in as an interactive-session hook.

## Consequences

- **Positive:** interactive latency and failure modes are unchanged — no new
  per-prompt process spawn, and no path by which a hook exit code can block or
  slow a session on this box or on any lane worktree. The governance gap closes
  honestly: "how does a prompt reach the fleet" now has a recorded answer, and
  the answer is checkable by a gate rather than by reading intent.
- **Negative:** the decision must be revisited deliberately before any project
  hook is added; a lane that wants a project hook will be stopped by
  `check-prompt-intake-declaration.sh` until it supersedes this record. Someone
  wanting prompt-level telemetry for interactive sessions cannot get it from a
  project hook under this decision — it would need its own ADR.
- **Neutral:** `.claude/settings.json` carries a `_`-prefixed
  non-schema key. Claude Code `2.1.278` tolerates it (measured: a session in a
  project whose settings carried the key ran normally, and a deliberately
  malformed project settings file did not stop a session either). The prose is
  duplicated in `.claude/README.md` so the declaration survives even if a future
  host version tightens unknown-key handling.
- **Follow-ups:** (1) add the index row to `docs/decision-records/README.md`
  once #1628/#1629/#1634 land (see *Index note*); (2) cross-reference this
  record from `docs/PAPERCLIP-ING-GAP-ANALYSIS.md`, which is held by open lanes
  #1629/#1630/#1610 at decision time and could not be edited here.

## Verification

Reproduce with `bash scripts/check-prompt-intake-declaration.sh` (the gate, which
provokes each of its own refusals), and with the host-level probe recorded on
issue #1516: a scratch project carrying a `SessionStart` hook and then a
`UserPromptSubmit` hook fired both on one real prompt, while the shape this
repository actually ships — the declaration plus `"hooks": {}` — wrote no marker
at all. The declaration is inert; the mechanism it declines to use is not.
