# Model Profiles & FinOps

> Canonical model-selection ladder for dispatching subagents on the **CMR program** — the hub itself
> and, once EPIC-01 lands, every governed spoke. `fleet/MANIFEST.tsv` records each issue's `tier`
> column (L0/L1/L2); this file explains what those tiers mean and how they map onto the two concrete
> worker models in the Copilot environment — **DeepSeek Flash** (mechanical) and **DeepSeek Pro**
> (judgment). Referenced by `docs/SOLUTION-CLASSES.md`, `docs/SME-PROFILES.md` and
> `docs/EXECUTION-PLAN.md`; the machine-readable resolver is `fleet/dispatch.sh model`.
> The global `CLAUDE.md` FinOps policy is the outer authority.

> **Explicit L0/L1/L2 → flash/pro map (legacy tier names).** L0 → **flash/LOW** · L1 → **flash/MED**
> by default, escalated to **pro/HIGH** when the issue's note marks security/IaC/debug (the floor,
> rule 5) · L2 → **pro/MAX** as the **advisor** — consulted, never the worker (rule 4). This is the
> same mapping `fleet/MANIFEST.tsv`'s header and `docs/EXECUTION-PLAN.md` state; `fleet/dispatch.sh
> model CMR-###` resolves it.

## The ladder — LOW / MED / HIGH / MAX

| Tier | Manifest tier | Claude Code | Copilot env (this assistant) | Use for | CMR example | Relative cost |
|------|---------------|-------------|------------------------------|---------|-------------|---------------|
| **LOW** (bulk) | L0 | `haiku` | **DeepSeek Flash** | lookups, file scans, mechanical edits, verification runs, routine docs, ledger/INDEX curation | `make verify` gate runs, doc ports (CMR-208, CMR-408), repo hygiene (CMR-107), prompt-lib port (CMR-502), health-report formatting (CMR-705) | $ (1×) |
| **MED** (mechanical) | L1 | `haiku` | **DeepSeek Flash** | single-file implementation, tests, review-lite, small refactors that follow an existing pattern | ADR process + template (CMR-109), drift-inventory data pull (CMR-110), pinning guidance (CMR-305), adoption-wave playbook (CMR-608) | $ (1×) |
| **HIGH** (complex) | L1 | `sonnet` | **DeepSeek Pro** | multi-file feature work, non-trivial debugging, security/secret/auth/IaC work | Terraform GitHub governance module (CMR-104), security baseline (CMR-105, CMR-108), auto-upgrade PR engine (CMR-402) | $$ (~10×) |
| **MAX** (decision) | L2 | `opus` | **DeepSeek Pro** | architecture tradeoffs, adversarial verification, ambiguous specs needing real judgment | taxonomy/schema freeze (CMR-201), blast-radius matrix (CMR-801), restricted-consumption model (CMR-803), SaaS escape hatch (CMR-805) | $$$ (~100×) |

> **Flash is the default. Pro is the escalation.** LOW and MED both run on DeepSeek Flash; HIGH and
> MAX both run on DeepSeek Pro — the difference is what they are asked to *do*, not which model.

> **Measured cost model (ADR-0040 / DR-069, doctrine correction #749).** The 1×/10×/100×
> column above is a **tier-ranking proxy**, not a session cost model — it orders tiers
> against each other, it does not predict what any one dispatch actually bills. The real
> per-session cost model comes from `metrics/frontloading-cost.tsv` (real per-turn
> `usage` token accounting, not the proxy): across 6 measured orchestrator sessions,
> `billed_equiv / turns` is **near-constant at 12–15k**, spanning two models and a 4×
> spread in `explore_calls`. **Cost is turns × conversation size — driven by
> turns-to-first-edit, not brief size and not cache-hit rate.** Two corrections to
> prior assumptions, both falsified by measurement:
> - **Cache-hit rate is not the lever.** Measured at 0.927–0.983 across the ledger —
>   already realized, not "the largest unrealized FinOps lever" (DR-068's original
>   framing; corrected in `board/epics/EPIC-26-ecosystem-deep-review.md` by #748).
> - **Brief size is not the lever.** `fleet/dispatch.sh hint` emits ~15 lines; there is
>   no 90% inside it. Compressing briefs optimises a rounding error.
> - **File-read volume is not the lever.** `explore_calls` spans 9–36 across the ledger
>   with no corresponding cost signal — it does not predict `billed_equiv`; `turns`
>   does. Reading fewer files is second-order.
>
> The target this drives: **≤5 turns-to-first-edit** (at 12–15k/turn, ≤5 turns ≈ 62k ≈
> the ADR-0040 ≤10%-of-baseline target — median `billed_equiv` for `phase=enforced` ≤
> 61,925 against a `phase=baseline` median of 619,252). The six-item brief-completeness
> checklist in `guardrails/prompts/dispatch.prompt.md` exists to hit that turn count, not
> to shrink the brief itself — see `guardrails/instructions/model-tier-discipline.md` §6.
>
> **Honest limitation (CMR-746):** this cost model is validated only for the
> `orchestrator` segment (long-running main-session dispatches). The widened,
> segmented measurement corpus (~395 transcripts) found the `lane-dispatch` segment
> (subagent/worktree sessions, which is what most of the tiers above actually dispatch
> to) **unmeasurable, not zero-cost or equivalent** — n=0 completed samples under the
> current edit-tool detector, because lane sessions frequently write files via a `Bash`
> redirect/heredoc/tee instead of the `Edit`/`Write` tool the window keys on. Do not
> read the ≤5-turn / 12–15k-per-turn numbers as validated for per-dispatch subagent
> cost until a better detector (ADR-0040 Phase 2) produces a measured `lane-dispatch`
> baseline. Full accounting: `metrics/frontloading-cost.tsv`,
> `docs/decision-records/ADR-0040-warm-start-frontloading-subsystem.md`.

> **Divergence note (deliberate).** The global `~/.claude/CLAUDE.md` FinOps ladder puts all of L1 at
> sonnet — a single bucket. CMR splits L1 into **MED (haiku)** and **HIGH (sonnet)** instead, because
> L1 is too broad a bucket to price uniformly: "single-file edit following an existing pattern" and
> "multi-file security refactor" are both L1 in the global scheme and differ roughly 10× in cost. This
> split is CMR-specific policy, recorded here so the two files read as deliberately consistent rather
> than quietly contradicting each other. `guardrails/instructions/model-tier-discipline.md` states the
> haiku default and the five escalation signals that move a dispatch from MED to HIGH; this file keeps
> owning the ladder tables below.

## Rules

1. Default **every** subagent to the cheapest model that can plausibly finish the job — **Flash** for
   LOW/MED, **Pro** for HIGH/MAX.
2. **flash-by-default rule (no pre-emptive pro):** Flash is the default for LOW and MED. Escalate to
   **Pro** (HIGH) only on **observed difficulty** — a Flash-tier agent failing, looping, or
   demonstrably unable to hold the constraints — never pre-emptively "to be safe". The security/IaC
   floor (rule 5) is the one exception where the default starts at HIGH.
3. State the tier + model + a one-line reason on every dispatch that isn't LOW, so cost tradeoffs stay
   visible. `fleet/dispatch.sh model CMR-###` prints exactly this line for any issue.
4. `advisorModel` stays at the top tier (MAX) and is **consulted, never the worker**.
5. Security, secrets, auth, and production IaC work never drops below **HIGH** (Pro). In CMR terms:
   anything that touches `infra/terraform/**`, repo access/consumption policy, secrets handling, or the
   SaaS boundary (CMR-105, 108, 306, 505, 606-govern, 703, 803–805) is never LOW/MED.
6. **DeepSeek is the Copilot environment's worker family** — Flash for LOW/MED, Pro for HIGH/MAX. Pass an
   explicit model only when the task genuinely needs a different tier; the manifest `tier` + note already
   encodes it.

## Escalation loop

```text
dispatch at flash (LOW/MED)
      │
      ▼
  finished + verified? ──yes──► done (report evidence)
      │ no
      ▼
  failed / looped / couldn't hold constraints
      │
      ▼
  escalate ONCE to pro (HIGH), quoting the observed failure as the reason
      │
      ▼
  still failing? ──► stop; re-scope the issue (smaller lane, clearer acceptance)
                    or file a blocker — never loop pro at rising cost
```

Escalate **once** on observed difficulty, never more. A second failure is not a signal to escalate
again — it is a signal to re-scope the issue or raise it for a MAX (L2/architecture) decision.

## Cost table (relative)

| Tier | Model | Relative cost | Typical spend |
|------|-------|---------------|---------------|
| LOW | DeepSeek Flash | $ (1×) | many parallel lookups/scans — batch hard |
| MED | DeepSeek Flash | $ (1×) | single-file work, tests, review-lite |
| HIGH | DeepSeek Pro | $$ (~10×) | multi-file / debug / security / IaC — one lane at a time |
| MAX | DeepSeek Pro (advisor) | $$$ (~100×) | architecture decisions — time-boxed, decision-only |

Multipliers are a FinOps heuristic (Flash is a fraction of Pro per token and iterates less).
**Cheapest capable wins** — a MED Flash run that passes is ~10× cheaper than a pre-emptive HIGH Pro run.

## CMR note — where the board sits on the ladder

The board is not uniform — issues span M0–M12 and later vendor/onboarding waves (CMR-001 upward).
Know your bulk vs your judgment spend before a train starts:

| LOW/MED-heavy (flash, batch cheap, parallelize hard) | MAX-heavy (pro, decision first, arch-sme, time-boxed) |
|---|---|
| CMR-006/007 board materialization (done) | CMR-001–005 M0 ratification (owner) |
| CMR-102, CMR-208, CMR-408, CMR-608 doc ports / playbooks | CMR-201 taxonomy + schema v1 freeze |
| CMR-107 `.github` hygiene | CMR-205 feature-flag & integration contract |
| CMR-110 drift inventory (data collection half → flash) | CMR-301 versioning policy ratify |
| CMR-601 harvest triage / INDEX | CMR-304 Terraform module distribution decision |
| CMR-609 future re-run ledger | CMR-504 agent-action gate design |
| CMR-705 health-report formatting | CMR-703 identity baseline, CMR-803 restricted consumption, CMR-805 SaaS escape hatch, CMR-806 SaaS blueprint |

Everything else in EPIC-01..06/08 is L1 implementation: **MED (Flash) by default, HIGH (Pro) when the
note marks security/IaC/debug or the task is multi-file** — a single lane, one SME, verify gate, done.
MAX issues are rarely "work" — they are a decision + a decomposition that unblocks a MED/HIGH train.

## FinOps guardrails

- Batch independent subagents **in parallel**; never serialize lookups (M6 harvest fans out across repos).
- **One issue = one subagent = one lane** (see `docs/EXECUTION-PLAN.md`). No two lanes touch the same
  `owns` glob. Conflicts are a FinOps cost, not just a correctness cost.
- Time-box MAX/architecture dispatch to **decisions**, not implementation — arch-sme hands the build to
  the owning lane's SME at MED/HIGH.
- Escalate flash → pro only after a cheaper tier has demonstrably failed — not before.
- Verify locally (`make verify` / the issue's `**Verify:**` command) before declaring done; a failed gate
  costs a re-dispatch at a higher tier.
- Re-check the board before standing down (never-idle doctrine).
- Audit spend fleet-wide with `fleet/dispatch.sh model --audit` — every manifest row's tier + model + cost
  in one table.

## Dual-role vendors & vendored-product FinOps

When CMR consumes a vendor in both capacities at once (module + vendored product —
`docs/VENDOR-DUAL-ROLE.md`), keep two separate budget lines:

- **Hub runtime budget (vendored product):** budget by resource kind, not by model tier. The
  code-indexing exemplar is a deterministic indexer — compute/storage/cron cost, **no LLM tokens** —
  and agent queries against its KB dispatch at LOW (Flash). If a vendored product embeds LLM calls,
  those calls follow this ladder like any agent dispatch.
- **Pattern-work budget:** direction-issue drafting = LOW (Flash) mechanical; template/class-marker/
  doctrine polish = MED (Flash) (`pattern` class in `docs/SOLUTION-CLASSES.md`); harvest + generalize
  (CMR-1002) = HIGH (Pro) minimum (secrets boundary, never LOW).
- **Vendor-side FinOps:** a CMR-1005-style direction asks the vendor to right-size BAU and report
  cost through CMR's observability hooks — the vendor's own spend stays in their lane.

## Quality class → model tier (consistent with `docs/SOLUTION-CLASSES.md`)

| Class | Dispatch tier | Review |
|-------|---------------|--------|
| `template` | LOW (Flash, mechanical) | self + qa-sme gate |
| `class` | LOW–MED (Flash) | qa-sme gate |
| `pattern` | MED (Flash; HIGH Pro if multi-file) | qa-sme gate |
| `enterprise` | HIGH (Pro) — security/IaC floor | qa-sme + security/iac-sme as applicable |
| `faang` | HIGH impl + MAX (Pro/advisor) review | qa-sme gate + multi-SME review |
| `elite` | HIGH impl + MAX (Pro/advisor) review | ADR recorded + provenance + qa-sme gate |

The `iac` mandate is not a tier — it is a **cross-cutting constraint applied at every tier**
(everything declared, flag-gated OFF by default, applied by the code-native runner/deployer SA — never the console).

## Known-answers, cache & frontloading (DR-069)

FinOps doctrine is only real if it is a mechanism, not prose a subagent can skip. Three
enforceable steps, in dispatch order:

1. **Check the known-answer cache (L1) + indexer KB (GR-17) before dispatching a KB question,
   at LOW (Flash) tier.** A subagent that is about to ask "what does the org already know about
   X" MUST first do a cheap local/L1 lookup and a GR-17 indexer-KB query — both at LOW — before
   any HIGH/MAX dispatch is allowed to re-derive the answer from scratch. This is the same rule
   `guardrails/instructions/local-code-first.md` states for debugging (GR-17); this section is
   the FinOps-side pointer to it so the two doctrines don't drift apart.
2. **Dispatch briefs carry pre-indexed static context, not a re-derivation prompt.** When a
   coordinator writes a subagent brief (per this repo's own Agent-tool guidance), it MUST
   frontload known facts, file paths, and prior findings already sitting in the KB/board/ADRs
   instead of asking the subagent to re-explore territory that has already been indexed. A brief
   that omits available pre-indexed context and asks a LOW/MED agent to re-scan the whole repo
   for something the KB already answers is a FinOps defect, not a style preference.
3. **New agents/sessions consume warm-start packs instead of re-deriving context from scratch.**
   Where a warm-start pack exists for the module/session being resumed (board state, prior
   decisions, open lanes), a new agent or session MUST load it first and only fall back to
   re-deriving context when no pack exists — recorded as a gap on the board (GR-20), not silently
   worked around.

**Provenance note on measured baselines.** DR-069 asks this section to cite measured baselines
with provenance — specifically figures on the order of "0.0105 ms L1 cache hits" and a
"63.2% → 80–92% L2 token reduction." A repo-wide search of `docs/`, `board/`, `guardrails/`, and
`controller/` for these figures (or equivalent FinOps measurement artifacts) at the time this
section was written turned up **no primary source in this repository** — no benchmark script,
recorded run, or measurement doc producing these numbers. Rather than invent provenance, this is
stated explicitly: **these baseline figures are not yet sourced in-repo.** Until a measurement
artifact exists (e.g. a benchmark under `controller/` or a recorded run linked from `board/`),
treat the specific numbers as unverified and do not cite them as fact; the three mechanisms above
stand on their own (cache-before-dispatch, frontloaded briefs, warm-start packs) independent of
which exact percentage the org eventually measures. Whoever runs the first real measurement should
replace this note with a citation to the run (script path + board issue) rather than editing the
numbers in place without a source.

Mechanical trace of this section: the conformance gate's `finops-doctrine` signal
(`guardrails/check-conformance.sh`) greps a target repo's `docs/MODEL-PROFILES.md` for this
section's own heading plus the three numbered mechanisms above, so "the doctrine is present" is a
checkable structural fact, not a claim.

**Escalation precondition (FinOps-D / #448).** DR-069's frontloading mechanism (item 2 above) is
only real if a brief that fails it can't be papered over by escalating tier instead. The
six-item "complete brief" checklist and its escalation precondition — "too hard for haiku" is
inadmissible unless the brief already satisfied all six — live in
`guardrails/prompts/dispatch.prompt.md` (the checklist + copy-paste template) and
`guardrails/instructions/model-tier-discipline.md` §6 (the precondition rule itself). This section
does not restate either; it cross-references them so DR-069's frontloading mechanism and the
escalation ladder's tier rules stay consistent with each other.
