---
description: "Dispatch template for CMR subagents. Goal-first (Goal → Constraints → Context → Steps → Verify). Render {{...}} fields before sending."
---

# SUBAGENT DISPATCH — {{agent}} · {{model_tier}}

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 9d61bf5

## Complete brief checklist (FinOps-D / DR-067 / DR-069)
A brief is **complete** only when all six items below hold. "This task was too
hard for haiku" is admissible as an escalation signal **only if** the brief
already satisfied this complete brief checklist (see
`guardrails/instructions/model-tier-discipline.md` §6, the escalation
precondition this checklist gives teeth). Escalating past an incomplete
brief is a FinOps defect, not a tier decision — complete the brief and
re-dispatch at haiku first; escalate only if it fails again on a complete
one. This is a fixed-prose section of the template (static region, DR-067
below) — the six items themselves never carry per-dispatch content.

**Why this checklist, and not a shorter brief or a cache tweak (ADR-0040 /
DR-069, doctrine correction #749):** measurement over real per-turn token
usage (`metrics/frontloading-cost.tsv`) found cost is `turns ×
~12–15k billed_equiv/turn`, near-constant across models and a 4× spread in
explore calls — **turns-to-first-edit is the lever**, target ≤5. Brief size
was checked and ruled out (`hint` emits ~15 lines; no 90% hides there); so
was cache-hit rate (already measured at 0.927–0.983, largely realized, not
"unrealized" as DR-068 originally framed it — see the correction in
`board/epics/EPIC-26-ecosystem-deep-review.md`, #748). Each of the six items
below removes one class of question the receiving agent would otherwise
spend a turn re-deriving by exploration — that is the entire mechanism by
which a complete brief lowers cost. This validation is confirmed only for
the `orchestrator` segment; the `lane-dispatch` segment most dispatches
using this template land in is currently **unmeasured** (n=0, CMR-746) —
follow the checklist because the mechanism (fewer exploratory turns) is
sound, not because the ≤5-turn number is yet confirmed for a typical
subagent dispatch.

1. **Goal frontloaded** — the concrete objective and what "done" looks
   like, stated in the first sentence or two, before any background.
2. **Exact file set** — the specific files in scope (`file_set`) *and* an
   explicit do-not-touch list (the lane's `owns` globs from
   `fleet/MANIFEST.tsv`). Not "the auth module" — actual paths.
3. **Exact verify command** — the issue's `Verify:` plus `make verify`,
   quoted verbatim so it can be pasted; real output is the evidence.
4. **A worked example of the expected diff shape** — a few lines showing the
   pattern to follow, or a named existing file to mirror. Removing this
   "what should this look like" inference is the single highest-leverage
   item for a cheap tier succeeding.
5. **Explicit constraints and non-goals** — what not to do, what is out of
   scope, which invariants hold (no commit/push, NG2, no `terraform apply`,
   no secrets).
6. **DR-067 context pack embedded, static-first** — via
   `fleet/dispatch.sh hint`, placed before any dynamic/delta content, with
   no dynamic tokens inside the static region (see the boundary rule
   immediately below).

### Copy-paste brief-completeness template
Paste this into a dispatch, fill every field, and do not send until all six
are checked — completing this is meant to be faster than escalating:

```text
[ ] 1. Goal (first line): <one sentence — the objective and "done">
[ ] 2. Files in scope: <path, path, ...>   Do-not-touch: <owns globs>
[ ] 3. Verify: <issue Verify: command, verbatim> && make verify
[ ] 4. Expected diff shape: <mirror file path OR a few example lines>
[ ] 5. Constraints/non-goals: <out of scope> | invariants: no commit/push,
       NG2, no terraform apply, no secrets
[ ] 6. Context pack: `fleet/dispatch.sh hint <id> [repo]` output embedded
       static-first, before this delta content
```

## Static/dynamic boundary (DR-067 / CMR-320)
A rendered brief has two regions, in this fixed order — **static first, delta
last** — so a provider's prompt cache can hit on the static prefix across
re-dispatches:
- **Static region**: the `Context:` pre-indexed pack for the target repo
  (`fleet/dispatch.sh hint <id> [repo]` → `catalog/indexer/context_pack.py`),
  plus this template's own fixed prose (Goal/Constraints/Steps/Verify
  sections above). This region MUST be byte-identical across re-dispatches
  for the same repo state — re-running the builder twice against an
  unchanged index must produce sha256-identical static-region bytes.
  **Dynamic tokens are forbidden inside the static region**: no timestamps,
  no run IDs, no dispatch/session UUIDs, no "generated at" stamps, no
  random ordering (all lists in the pack are key-sorted). When no index
  exists for the target repo, the pack states that absence explicitly
  (`index: absent (...)`) — it never fabricates content to fill the gap.
- **Dynamic/delta region**: everything issue- and run-specific — the
  `{{issue}}`/`{{req}}` fields, the MANIFEST-derived `DISPATCH ...` line
  (model tier, lane, note), and any audit-log timestamp. This region always
  renders AFTER the static region, never interleaved with it.

## Goal
Implement exactly the task below and prove it with real command output. Success =
the acceptance criteria are met and `make verify` passes. Done is verified, never claimed.

## Constraints (non-negotiable)
- Work only in the declared `file_set`; `do_not_touch` files are owned by other lanes — never edit them.
- Match existing style; smallest focused diff; no `TODO`/`FIXME`/debug prints; no unused imports/vars; no placeholder secrets.
- Never commit or push; never merge or approve your own work (NG2); never `terraform apply` (PR → plan → code-native runner apply, flag-gated OFF by default).
- No secrets in code or history — env/GSM only; gitleaks runs in the gate.
- Respect lane ownership from `fleet/MANIFEST.tsv`; one issue = one lane, no shared files.

## Context
- Repo: CMR hub — IaC-driven control plane for the `kushin77` spoke ecosystem. Canonical rules in `AGENTS.md`; standards in `board/` and `docs/`.
- Issue: {{issue}} (R# {{req}}). Epics in `board/epics/` are the source of truth.

## Steps
1. Read `AGENTS.md`, the issue, and its epic; restate the goal + acceptance criteria.
2. Confirm lane ownership (`fleet/MANIFEST.tsv`); list the exact files to touch.
3. Implement the minimal change and format it.
4. Run the Verify commands below; fix failures until green.

## Verify
- `make verify`
- Paste each command and its output as evidence — never an unverified "done".

No-questions doctrine: apply `docs/DEFAULTS.md`; escalate only per GR-22 (secrets / apply / merge / irreversible).
