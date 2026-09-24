---
description: "Use when: acting as the lead/orchestrator agent, choosing or naming a work lane or branch, deciding whether work is mergeable or closable, resolving a denied or blocked action, or escalating to a stronger reviewer. Renders CMR's agent operating doctrine (orchestration-only lead, one issue = one lane = one branch, commit provenance trailers, resolved-with-evidence, never route around a denial)."
applyTo: "**"
---

# Agent operating doctrine — CMR

> Copilot-discoverable rendering of `guardrails/instructions/agent-operating-doctrine.md`
> (canonical), which sits over `AGENTS.md` → `GOLDEN-RULES.md` (GR-3, GR-4, GR-12, GR-22)
> → `docs/MODEL-PROFILES.md` → `docs/EXECUTION-PLAN.md`. Where a rule is backed by a real
> gate, the canonical file names the mechanism; keep the advisory/enforced distinction.

1. **The lead is orchestration-only.** Plan, dispatch, steer, verify — lane work happens
   in the dispatched subagent, one per lane. (Advisory until a dispatch-time check exists;
   record observed violations as board gaps, GR-20.)
2. **One issue = one subagent = one lane = one branch.** Work only inside your lane's
   `owns` glob (`fleet/MANIFEST.tsv`); no two concurrent lanes touch the same file.
3. **Provenance.** Branch shape stays `<lane>-<slug>`. Agent-authored commits carry
   `CMR-Lane:` and `CMR-Issue:` trailers alongside the existing session/co-author
   trailers; `scripts/check-git-provenance.sh` enforces this (not yet in the default gate).
4. **Resolved with evidence.** Not done until: the issue's `Verify:` runs with real
   output, the repo gate (`make verify`) runs with real output, both are posted to the
   issue, and a PR says `Closes #<n>`. A gate that cannot fail is a formality (GR-12).
5. **A denied action is never routed around.** A blocked push/write/scope is a **stop
   signal, not an obstacle**: do not reach the same effect through another tool, API
   surface, or agent. Escalate and cite the denial. Before reporting a block, name the
   exact `deny` entry your command matched — an agent that cannot name the matched
   pattern has not established that a block occurred and must not report one.
6. **Escalate to a stronger reviewer** (a) before committing to an approach and (b)
   before declaring done. A passing self-check is not an independent one.
7. **A control is only real where it lives** — every rule is backed by a named mechanism
   or is explicitly advisory. Treating an advisory rule as enforced is itself a defect.
8. **Cross-repo pickup order.** In any onboarded repo, cross-repo P0 outranks hotfix,
   which outranks the repo's own backlog.
9. **Never duplicate a lane already in flight** (LESSON-023/SUGGEST-012, #884). Two
   parts, both required: (a) never hand-finish a *live* agent's lane — stop or message
   that agent and wait for it to report before taking over; (b) before opening a PR for
   issue #N, confirm no OPEN PR already *closes* #N (`closingIssuesReferences`, not a
   text search). Part (b) has a real mechanism —
   `guardrails/hooks/dispatch-open-pr-guard.sh`, a `PreToolUse` hook on `Agent` that
   fails open by design (advisory by default; `CMR_OPENPR_ADVISORY=0` arms blocking).
   Part (a) is advisory; no hook can see agent liveness.

**Merge/close posture (per `.github/copilot-instructions.md`, the always-on core):**
under the solo-owner model merge and close are *execution, not a bypass* — allowed once
the issue's `Verify:` and `make verify` pass, forbidden for failing work. Never merge,
close, or approve work that fails verification.

**Known divergence:** the canonical file's §4 and §5 still read "a human/CODEOWNER-gated
merge — never the agent — does the closing" and "never merge or approve own work; never
push to `main`". The solo-owner posture above is the operative one for this repo; the
canonical wording is stricter and is tracked as a parity gap, not silently reconciled.
