# Golden Rules — agent-orchestrator (product spine)

The **operating spine** of the AI-agent-orchestration control plane: the
non-negotiable rules that govern both *how this repo is developed* and *what
the platform enforces* on tenants, agents, and the control plane itself.

This file is the **canonical product spine** (issue #7 / "03 Golden rules +
policy spine"). The repo's [`AGENTS.md`](../AGENTS.md) and per-runtime mirrors
defer to it for golden-rule content; this lane does not edit those files — the
linkage is declared here instead.

Cannibalized and **adapted** (not copied) from `kushin77/CMR` `GOLDEN-RULES.md`
(GR-1..24), `kushin77/leaderboard` `docs/reference/RULES.md`,
`kushin77/shared-frontend` `docs/GOLDEN-RULES.md`, and
`kushin77/shared-governance` `GLOBAL_STANDARDS/**`. Provenance is tracked in
[`CANNIBALIZATION.md`](CANNIBALIZATION.md) (issue #8).

---

## Scope — what this product governs

`agent-orchestrator` is a multi-tenant SaaS control plane that organizes,
governs, and manages commercial AI agents (Claude, DeepSeek, Copilot, Gemini,
local Ollama, …) for enterprises. The golden rules below bind:

- **The platform** — the five pillars (Agent Registry & Profiling, Model
  Gateways, State-machine execution, Security & guardrails, Observability) plus
  cross-cutting tenant identity/RBAC, the control plane/portal, and autonomous
  ops/governance. Source of truth:
  [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **The tenants** and their **agent orgs** the platform hosts (isolation,
  budgets, audit, guardrails are product guarantees, not tenant options).
- **The delivery of this product** — this repository's own development,
  dispatch, and merge behavior (one lane per issue, honest gates, evidence
  before merge), so the platform is *built the way it governs*.

Rules fall into two parts: **Part A — delivery & repo spine** (how work on this
product is done) and **Part B — platform & SaaS spine** (what the product
enforces at runtime). Part B rules are *product contracts*: the pillar that owns
each surface must ship the stated mechanical verification with it, flag-gated
OFF (AO-GR-6), not add it later.

## Precedence

1. Within this repository, [`AGENTS.md`](../AGENTS.md) is the canonical,
   model-agnostic doctrine; this spine is the golden-rule content it points to.
   Where this file and a policy/doc disagree, **the golden rule wins**.
2. This repo is a governed fleet repo: where **this** spine is silent, the hub
   golden rules (`kushin77/CMR` `GOLDEN-RULES.md`, GR-1..24) bind.
3. Rules are terse on purpose; each exists because its absence has caused (or
   would cause) a recorded failure in the `kushin77` fleet. Each rule names the
   failure it prevents and the mechanical check that proves it.
4. ADRs (`docs/decision-records/`) ratify architecture decisions; a rule may
   reference an ADR, but a rule is not superseded by a doc — only by a newer
   rule or a recorded decision.

## Rule format

Every rule has exactly three parts:

- **Rule** — the terse, non-negotiable statement.
- **Why** — the failure it prevents.
- **Verify** — a mechanical check that can genuinely fail (no-false-green,
  AO-GR-4). Where the check belongs to a not-yet-built pillar, the pillar issue
  must ship it with the surface — a rule whose verification "cannot run yet" is
  stated honestly as such, never silently assumed green.

Part B rules (AO-GR-12…20, the platform/SaaS spine) carry a fourth part:

- **Control** — `**Control.** modules: `a/`, `b/` — controls: `check-x.sh`,
  `suite:dir``. Names the enforcing gate control(s) and the implementing
  module(s). It is machine-checked: `scripts/check-control-coverage.sh`
  parses this line and requires its modules and controls to be the exact same
  set as the rule's row in `scripts/control-coverage.tsv`, in both
  directions, so the spine line and the map cannot drift apart (#873).

Part C rules also carry a fourth part, **Origin**, unrelated to Control (see
Part C below).

## Policy spine (adopted policies)

Issue #7 mandates that the policy spine explicitly adopt six policies. They are
each a golden rule (or composition of two) in this file — not prose aspirations:

| Policy | Golden rule(s) | What it means here |
|---|---|---|
| **Flag-gated OFF** | [AO-GR-6](#ao-gr-6--flag-gated-off-by-default) | Every new surface ships invisible until deliberately enabled. |
| **Verify with evidence before merge** | [AO-GR-3](#ao-gr-3--verify-before-done) + [AO-GR-11](#ao-gr-11--merge-governance-evidence-gated) | Merge only on green `make verify` with actual output attached. |
| **No-false-green** | [AO-GR-4](#ao-gr-4--no-false-green-gates) | Every gate can genuinely fail; formalities are rejected. |
| **Control plane never executes** | [AO-GR-12](#ao-gr-12--control-plane-never-executes) | The platform decides and directs; it never does the work it orchestrates. |
| **Independent auditor** | [AO-GR-13](#ao-gr-13--independent-auditor) | Agent output is verified by an independent party that runs the decisive check. |
| **Separation of duties** | [AO-GR-14](#ao-gr-14--separation-of-duties) | No single actor both performs and attests; prod actions need an independent step. |

---

## Part A — delivery & repo spine

### AO-GR-1 — Everything is tracked in a GitHub issue

**Rule.** Every change tracks in a GitHub issue opened **before** the work and
closed **with evidence**. Commits reference the issue
(`Refs kushin77/agent-orchestrator#<n>`); PRs close it (`Closes #<n>`).

**Why.** Untracked work is work nobody can find or audit later across a
multi-repo fleet. A commit without an issue reference is a conformance finding,
not a judgement call (CMR GR-2 / XR-004).

**Verify.**
- Every merged commit message contains `Refs kushin77/agent-orchestrator#<n>`
  (mechanical scan of `git log`).
- Every PR body contains `Closes #<n>`.
- Closed issues carry an evidence comment (the gate output that proved the
  change).

### AO-GR-2 — One issue = one lane; no two lanes share a file

**Rule.** Work only the files your issue owns; one issue = one lane = one
branch. Smallest focused diff; no unrelated edits.

**Why.** Parallel lanes editing the same file conflict and silently clobber each
other's work (recorded at scale in the fleet). Disjoint files make parallel
dispatch safe (CMR GR-3).

**Verify.**
- The PR diff touches only lane-owned paths, per
  [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md).
- A diff crossing into another lane's files is rejected at review/merge.

### AO-GR-3 — Verify before done

**Rule.** "Done" requires evidence: the issue's `Verify:` command **and**
`make verify`, with **actual output**. Never claim done on an unverified run.

**Why.** An unverified "done" is the failure the entire review chain exists to
catch — it propagates as fact into every downstream lane (CMR GR-12; leaderboard
R1 "verify live before claiming done").

**Verify.**
- `make verify` exits 0 in the worktree, output pasted on the PR.
- The issue's `Verify:` command (if any) is run and its output reported.

### AO-GR-4 — No-false-green gates

**Rule.** Every check in a gate can genuinely fail. A check whose pass and
gave-up paths produce the same exit code is a **formality** and is rejected.

**Why.** A gate that cannot fail protects nothing — worse, it is *worse* than no
gate, because it looks like coverage while guaranteeing none (leaderboard R8/R16;
testing-suite formality findings).

**Verify.**
- New checks are probed on a known-bad input and observed to exit nonzero.
- No `SKIP`-counts-as-pass, no `|| continue` that hides an uncounted absence,
  no "optional" branch around a blocking gate.
- `make verify` includes a formality/self-match scan.

### AO-GR-5 — Fully IaC; no console clicks

**Rule.** Infrastructure is declared (Terraform / Cloud Build), never clicked in
a console. No ad-hoc `terraform apply`.

**Why.** Clicked infrastructure is undeclared, unreproducible, and ungoverned —
it cannot be reviewed, drifted-checked, or revoked (CMR GR-5; owner IaC
mandate).

**Verify.**
- `infra/terraform/**` + `infra/cloudbuild/**` hold all declarations; config
  validates (`terraform validate`).
- Live changes arrive only via PR → plan → apply by the deployer identity.

### AO-GR-6 — Flag-gated OFF by default

**Rule.** Every new product surface ships **behind a feature flag defaulting to
OFF**. Nothing is tenant-visible until deliberately enabled via the rollout
pipeline (issue #45).

**Why.** Shipping a half-built surface ON leaks incomplete or insecure behavior
to tenants; flag-gating makes rollout a reviewable, reversible act instead of a
deploy accident (EPIC-00 rollout doctrine).

**Verify.**
- Every new surface's configuration default is `disabled`/`off` (declared in
  IaC/config, not inferred).
- Enabling a surface is a deliberate, reviewed change through the rollout
  pipeline — never an ambient side effect of deploy.

### AO-GR-7 — No secrets in code, files, or git history

**Rule.** Secrets live in env / secret managers only. Never commit a real token
or key; never pass secrets through argv or history.

**Why.** One committed key is a fleet-wide incident, and once a secret is in git
history it is effectively public forever (CMR GR-6).

**Verify.**
- `make verify` runs a mechanical secret scan (always-on) — it exits nonzero on
  a leaked secret.
- gitleaks (where installed) and the `.gitleaks.toml` allowlist stay green.

### AO-GR-8 — SemVer is law

**Rule.** Releases are annotated `vX.Y.Z` tags on `master` at a gate-green
commit. Nothing force-pushes breaking changes downstream.

**Why.** Consumers and tenants pin versions; an untagged or force-pushed release
cannot be traced, reverted, or audited (CMR GR-7; RELEASING.md).

**Verify.**
- `git tag` releases are annotated `vX.Y.Z`; the tagged commit is gate-green.
- No force-push to `master` (branch protection, issue #6).

### AO-GR-9 — AI guardrails required on every governed surface

**Rule.** Every surface this repo governs ships AI instruction files (AGENTS.md
/ CLAUDE.md / .cursorrules / mirrors) so agents inherit the architecture,
security, and standards; AI output clears the same gates as human output.

**Why.** Agents act on what the repo tells them; without committed instruction
files they guess, and an agent that guesses inherits nothing (CMR GR-9;
EPIC-05 instruction layer).

**Verify.**
- Instruction files exist and agree with this spine (spot-check that an agent's
  first answer reflects the guardrails).
- AI-authored PRs pass `make verify` and declare runtime + mode.

### AO-GR-10 — Provenance for cannibalized assets

**Rule.** Every harvested/cannibalized asset records its source (repo, path,
license) in [`CANNIBALIZATION.md`](CANNIBALIZATION.md) **before** reuse.

**Why.** Legal and maintainability traceability: the index is how reuse stays
accountable, and an asset with no recorded source cannot be relicensed or
re-derived (CMR GR-10).

**Verify.**
- New assets derived from another repo appear in the cannibalization index with
  repo + path + license.
- No un-indexed harvest ships (index is gate-checked, issue #8).

### AO-GR-11 — Merge governance: evidence-gated

**Rule.** Every change lands via a PR against `master` (squash merge) — never a
direct push. Merge happens **only after** green verification evidence (AO-GR-3);
**never merge failing work**. Under the owner autonomous-merge mandate
(2026-09-07), an agent merges its own PR after green evidence — verification
replaces the human reviewer; it does not replace the requirement for evidence.

**Why.** The PR is the audit seam; every merge without green evidence erodes the
gate chain one step at a time. Evidence-before-merge is what makes autonomy safe
(CMR GR-4 adapted; GOVERNANCE.md §3; issue #43 codifies the independent SME
reviewer for the product).

**Verify.**
- `make verify` green + output attached before every merge.
- No direct push to `master`; merges are squash PRs with the `Refs`/`Closes`
  chain intact (AO-GR-1).
- A red gate blocks the merge and the next dispatch wave.

---

## Part B — platform & SaaS spine

> These are product contracts. The pillar issue that builds each surface must
> ship the listed verification with it (flag-gated OFF), so governance is
> **platform-enforced**, not doc-only (leaderboard R8).

### AO-GR-12 — Control plane never executes

**Rule.** The control plane **decides, directs, routes, and verifies** — it
never executes tenant or agent work itself: it does not write tenant code, run
agent tasks, or do the work of a lane it orchestrates.

**Why.** An orchestrator that does the work it orchestrates erodes boundaries,
blurs accountability, and becomes the single point that both decides and
executes — the exact anti-pattern that produced a 40% soldier success rate
nobody acted on (leaderboard R18: "the commander decides and dispatches; it does
not write the code").

**Verify.**
- Dispatch flows from the control plane route to engine/agent lanes; the
  control-plane service exposes no tenant-work executor capability.
- A control-plane change that adds an "execute work" path fails review
  (structural/static check once CI lands, issue #6).

**Control.** modules: `control-plane/` — controls: `check-control-functions.sh`, `check-control-verbs.sh`

### AO-GR-13 — Independent auditor

**Rule.** AI/agent output is verified by an **independent** party that executes
the decisive check itself — never by the author alone, never on self-report. A
verification command that times out is a FAILURE, not a pass; report the literal
exit code.

**Why.** Self-review is structurally blind: in a recorded 9-agent wave,
self-review found 0 of 8 wrong claims while cross-review found 8 of 8
(leaderboard R5/R15). An auditor that cannot be overruled is just a slower
source of the same errors — the audit must be evidence, checked against the
artifact's boundary, not its prose.

**Verify.**
- Audit verdicts are published and reference the command that proves them.
- The audit log is consulted **before** retrying failed work (leaderboard R12) —
  a published re-check command outranks any agent's reading of the code.

**Control.** modules: `governance/merge/`, `governance/lifecycle/` — controls: `check-landing.sh`, `suite:governance/merge`

### AO-GR-14 — Separation of duties

**Rule.** The party that authors or performs a change is never the sole party
that attests to it. Production-affecting actions require an independent approval
or verification step (product: tenant admin ≠ operator ≠ auditor; delivery:
merge requires independent evidence, not self-attestation).

**Why.** SoD is what makes AO-GR-13 real — without it, "independent audit"
collapses into self-attestation and the gate is a ceremony (shared-governance
approval-gate & agent-orchestration-model; leaderboard R5).

**Verify.**
- Product roles are separated in the identity model (issue #36): a tenant admin
  cannot self-attest its own agent's audit trail; an operator cannot override a
  budget it set without a second role.
- Delivery: an agent that authored a change cannot be the sole verifier of it —
  under autonomous merge the *independent* verification evidence is the second
  party (AO-GR-3/AO-GR-11).

**Control.** modules: `governance/merge/`, `identity/rbac/` — controls: `check-authority.sh`

### AO-GR-15 — Tenant isolation is structural

**Rule.** Tenants are hard-isolated across data, model routing, budgets, audit,
and memory. Isolation failures **fail closed**, are **detected first**, and are
repaired opt-in — never silently.

**Why.** A tenant-boundary crossing is the catastrophic failure of a
multi-tenant control plane; detect-first beats a silent breach that is
discovered months later (issue #30, tenant-isolation integrity).

**Verify.**
- Every schema/query carries tenancy; an unauthenticated or foreign-tenant
  access attempt is denied (fail-closed).
- A cross-tenant isolation test suite ships with the tenancy surface and a
  known-bad probe fails it (issue #30).

**Control.** modules: `guardrails/isolation/`, `telemetry/ledger/`, `identity/edges/` — controls: `suite:guardrails/isolation`, `suite:telemetry/ledger`

### AO-GR-16 — DLP + prompt-injection defense on every model interaction

**Rule.** Every outbound model call passes a scrub/egress guard; inbound
untrusted output is re-validated before it lands in tenant/agent state;
prompt-injection defenses are required, with HMAC per call where applicable.

**Why.** External LLMs are **untrusted consultants**, never trusted insiders:
outbound, no secret/PII/proprietary detail may cross the perimeter; inbound,
external output must not be trusted verbatim (shared-governance
external-llm-egress-policy; issue #27).

**Verify.**
- Egress/scrub guard runs per call with block/redact behavior that is itself
  tested (a blocked payload aborts the call).
- Injection-defense tests include **negative controls** — a probe proven to fire
  (issue #27; AO-GR-4).

**Control.** modules: `guardrails/dlp/`, `guardrails/chat/` — controls: `check-chat-guardrails.sh`

### AO-GR-17 — Tamper-evident audit ledger

**Rule.** Every agent action and policy decision is recorded on an
**append-only, tamper-evident, per-tenant** ledger (hash-chained, encrypted
payloads). One event per action.

**Why.** "Who did what, under whose policy, at what cost" is the product's core
promise to tenants; a mutable log is not an audit log and a per-tenant chain
prevents cross-tenant forgery (issue #31; shared-governance agent-action /
telemetry contracts).

**Verify.**
- Ledger integrity check: the per-tenant hash chain verifies end-to-end.
- Append-only is enforced at the storage layer; a tamper attempt breaks the
  chain and fails the check (issue #31).

**Control.** modules: `telemetry/ledger/`, `telemetry/audit/` — controls: `check-audit-read-model.sh`, `suite:telemetry/ledger`

### AO-GR-18 — Per-tenant budgets, quotas, and a kill switch

**Rule.** Every tenant/agent runs within declared budgets and quotas with a
global kill switch. Metering is the mechanism; enforcement is the guarantee.

**Why.** Unbounded token spend is a FinOps and abuse exposure, and a tenant
runaway must be containable by a single switch, not a support ticket
(issue #34; CMR FinOps doctrine; shared-governance telemetry-budget).

**Verify.**
- Metering → budget-enforcement path is exercised: a budget-violating call is
  blocked and metered.
- The global kill switch halts the tenant/agent end-to-end (issue #34).

**Control.** modules: `gateway/finops/`, `telemetry/chat/` — controls: `check-chat-finops.sh`, `check-metering-parity.sh`

### AO-GR-19 — Guard honesty: tri-state + negative controls

**Rule.** Every guard/policy gate returns an honest **BLOCK / WARN / LOG**
tri-state, defaults fail-closed, and ships with negative controls proving it can
fire.

**Why.** A guard that cannot fire or cannot be trusted to block gets bypassed —
and a bypassed guard is worse than none (issue #28; leaderboard R14 negative
control doctrine).

**Verify.**
- Every gate emits an explicit tri-state; nothing logs "OK" on a path that never
  ran.
- Each guard has a negative-control fixture (a probe proven to fire it) in its
  test suite (issue #28).

**Control.** modules: `guardrails/policy/`, `guardrails/honesty/` — controls: `check-negative-controls.sh`, `check-guardrail-controls.sh`, `check-policy-schema.sh`

### AO-GR-20 — Private by default

**Rule.** Tenant data, agent orgs, and platform surfaces are **private by
default**. Anything public is a separate, deliberate product surface — never the
default posture of the control plane.

**Why.** Private-by-default is the baseline trust posture for a multi-tenant
control plane; a public surface is a distinct threat model and must be opted
into, not stumbled into (CMR GR-8 / SaaS escape-hatch ADR-0010).

**Verify.**
- Default access is private (declared in config, not inferred); any public
  surface requires an explicit flag + separate security review.
- Public/private posture is part of the surface's gate (issue #37 proxy
  allowlist boundary).

**Control.** modules: `infra/feature-flags/`, `portal/config/` — controls: `check-feature-flags.py`

---

## Part C — fleet autonomy spine

Rules for the autonomous loop itself. Every one of these exists because its
absence was **measured** on this box, not hypothesised: on 2026-09-14 the fleet
ran 16 hours of gate-stacking, executed pre-fix code while reporting healthy,
and shelved 46 commits across 31 lanes. A rule here is advisory until its gate
ships — and per AO-GR-4 that gap is stated, never implied-enforced.

### AO-GR-21 — Bounded work: no queue item is retried forever

**Origin.** Issue #723 (measured 2026-09-14; the wedge itself was #366). The A2A
half is issue #754.

**Rule.** Every dispatched directive carries an **attempt budget** with
exponential backoff and a **terminal dead-letter state**. A refusal is a state
transition, not a reason to try again next tick. A directive that exhausts its
budget moves to the dead-letter mailbox and is **never re-read**; the brain is
escalated exactly once. Retiring an order is **a protocol operation, not a file
operation**: `control:drop` (routed by `fleet/control.py drop --directive <id>
--reason "<text>"`) tells the sister to dead-letter a named directive and acks
the sender. The automatic path and the verb are **one implementation**
(`runaway.dead_letter`) writing **one record shape**, so they cannot diverge.
Reaching into `.fleet/inbox/` and `mv`-ing an order aside is **legacy
remediation, superseded** — it races the loop's mid-`watch` reader, loses the
attempt history and the reason, bypasses the channel (no ack, no audit) and
cannot be done by an agent at all.

**Why.** `fleet/terminal.py` left a directive **PENDING** on a refused claim and
re-read it every cycle with no attempt counter, no backoff and no dead-letter
(`report_once` deduped the *escalation*, not the *attempt*). Measured: **49
concurrent `make verify` runs, 43 stacked in two worktrees, ~16 hours**; the
wedge that caused it (#366) was itself re-dispatched in the same loop. A work
queue with no dead-letter is an infinite loop with extra steps. The runaway fix
was then itself performed **out of band** — 23 directives swept to
`/tmp/dead-directives/` by `mv`, an operator reaching into another process's
queue, which is why the verb exists: the fleet is agent-to-agent, and an agent
that cannot tell its peer "this order is dead" is not an agent, it is a script.

**Verify.**
- Per-directive attempts are persisted and survive a loop restart; attempts are
  spaced by backoff; exhaustion dead-letters the directive.
- `bash scripts/check-runaway-guard.sh` provokes a directive that always fails
  and **must** observe it dead-lettered, never re-dispatched (AO-GR-4: the
  check can genuinely fail).
- `bash scripts/check-dead-letter.sh` drives `control:drop` against the real
  tree, proves the verb and the automatic path write the **same record shape**,
  proves an **undropped** directive is still returned (the negative control),
  and mutation-proves the retire path by refusing a mutant that leaves the order
  in the inbox (AO-GR-4).

**Applies to the watchdog itself (#773).** A *supervisor* is not exempt from its
own rule. Every self-healing action the watchdog takes is bounded the same way —
an attempt cap with backoff, one escalation, then a terminal **parked** state —
because a remedy that cannot change the value it compares is a runaway, not a
repair. Measured 2026-09-15: the watchdog respawned a rung **132 times in one
night** on a mismatch its own remedy could never clear, and did no work at all.

### AO-GR-22 — One gate per worktree, and the gate is admission-controlled

> **Scope note 2026-09-21 (single-developer method).** The composite gate on a PR branch is the code-only lane venue and is advisory for landing; box-state checks run in `make master-attestation`. The concurrency cap below still applies to every `make verify` run.

**Origin.** Issue #724 (measured 2026-09-14: 49 concurrent gates).

**Rule.** At most **one** composite gate (`make verify`) runs per worktree at a
time, bounded by a box-wide concurrency cap. Work that cannot acquire a permit is
**parked, not started**.

**Why.** `make verify` had no admission control, so a re-dispatch loop could
start gates without bound. Measured: 49 concurrent gates, zero idle CPU, and
long gates SIGTERM'd by neighbouring load — which is indistinguishable from a
test failure and corrupts the evidence chain. A gate that can be started an
unbounded number of times will be.

**Verify.**
- A per-worktree lock refuses a second concurrent gate (released on signal and
  on crash, with stale-lock detection naming the owner).
- A negative control starts two gates in one worktree and proves the second
  refuses.

### AO-GR-23 — No work is invisible: commit is pushed before it is gated

**Origin.** Issue #740 (measured 2026-09-14: 46 unpushed commits, 31 worktrees).

**Rule.** A lane **pushes its branch as soon as it commits** — before the gate,
never after. A committed change that exists only locally, or only in a worktree,
is not work: it is unsheltered data. The dispatch loop must not be able to end a
cycle with an unpushed commit.

**Why.** The loop pushed only on full success (runner exit 0 **and** gates
green), so a runner failure stalled every lane at "committed, never pushed".
Measured: **31 worktrees holding 46 unpushed commits**, including **5 lanes on
closed issues whose work `origin/master` did not contain**. An unpushed branch
is invisible to the board *and* to `governance/reconcile`, which can only
reclaim or park what reaches a remote — so this work was shelved by accident,
and for a closed issue nobody ever returns.

**Verify.**
- After any loop cycle, no worktree has commits ahead of `origin/master` that
  have no matching remote branch; a stranded lane is reported **by name**.
- `bash scripts/check-lane-stranded.sh` provokes a local-only commit and must
  fail it.

### AO-GR-24 — A wave is provably file-disjoint before it is dispatched

**Origin.** Issue #740 (measured 2026-09-14: 27 collisions across 14 lanes).

**Rule.** Before dispatching a wave of parallel lanes, the dispatcher computes
each lane's file set and **refuses to dispatch two lanes whose sets intersect**.
Collisions are resolved by serialising the wave or by re-scoping the issue —
never by fanning out and rebasing later.

**Why.** Fan-out by *issue* without collision-aware planning produced **27
source-file collisions across 14 lanes**: six children of one epic all edited the
same seven files (`Makefile`, `control-plane/control/cli.py`, `scripts/verify.sh`,
`governance/dispatch/cli.py`, `governance/dispatch/focus.py`,
`scripts/check-epic-focus.sh`, `test_focus.py`). That is AO-GR-2 violated in
bulk, and it means **raising agent count multiplies conflicts, not throughput** —
because the policy that would have prevented it was itself one of the colliding
lanes.

**Verify.**
- The ready wave is pairwise file-disjoint by construction; a negative control
  presents two colliding children and the dispatcher **refuses the second**.
- Max-agents fan-out is **blocked** until this check is green — the raising
  change and this gate land together or not at all.
- **Landed (issue #718).** `fleet/capacity.py` resolves the fan-out as
  `min(pool, disjoint ready lanes, resource ceiling)` and the loop holds a
  directive it cannot take, naming the binding bound;
  `scripts/check-capacity-gate.sh` **provokes** this rule's control — two ready
  lanes claiming one file, the later **held** and the collision named by lane and
  path — and then requires the relaxed pair admitted, so the two paths cannot
  share an exit code. The rule is therefore mechanically enforced, not merely
  declared (see `docs/EXECUTION-PLAN.md` §8).

### AO-GR-25 — Drift is measured against the remote, and never fails open

**Origin.** Issue #739 (measured 2026-09-14: the loop ran pre-fix code while
reporting `healthy`).

**Rule.** A running loop's commit is compared against **`origin/master`** — never
against the local checkout, which may itself be the stale side. An unreadable
HEAD is **CANNOT-ASSESS**, never healthy. When the running commit differs from the
remote, the rung is **drifted** — and *which* commit is stale decides the remedy:

* the rung is not on the local HEAD either ⇒ the **rung** is stale ⇒ **respawn**;
* the rung **is** on the local HEAD ⇒ the **checkout** is stale
  (`checkout-behind`) ⇒ **fast-forward the checkout** (`git fetch` +
  `git merge --ff-only`), then one respawn. A respawn alone re-executes the same
  checkout and cannot change the compared value (#773, AO-GR-21).

**Why.** `fleet/watchdog.py` compared the loop's heartbeat commit to
`channel.head_commit()`, which reads the **shared checkout**. With the checkout
stale (the normal state here) both sides were the *same old commit*, so the loop
reported `sister: healthy` while executing code from **before a merged fix** — a
fix that therefore could never reach the running fleet. The comparison was also
guarded by `head != "unknown"`, so an unreadable HEAD **silently disabled drift
detection entirely**. A control that cannot fail is a formality (AO-GR-4); a
control that fails *open* is worse than none.

**Verify.**
- The watchdog reports DRIFTED when the loop's commit differs from
  `origin/master`, even when the local checkout equals the running commit (a
  negative control proves this exact case).
- `HEAD == unknown` ⇒ CANNOT-ASSESS, never healthy; the watchdog line names both
  commits.
- `bash scripts/check-fleet-drift.sh` runs the real classifier against the
  measured case and mutation-proves itself: a mutant restoring the local-HEAD
  baseline, and one restoring the fail-open `head != "unknown"` guard, must each
  be caught. It is wired into `scripts/verify.sh` as `fleet-drift`.
- The remedy is bounded and named (#773): a stale checkout is fast-forwarded
  rather than respawned, no remedy is retried past its attempt cap, exhaustion
  escalates **once** and parks the rung, and a busy rung is recorded as *pending
  drift* instead of being dropped every tick. `bash
  scripts/check-watchdog-bounded.sh` proves it against a real scratch repository
  and two mutants of the real source, and is wired as `watchdog-bounded`.

**Baseline tradeoff.** The baseline is the *fetched* `origin/master`
remote-tracking ref; the watchdog does **not** fetch on every tick (a 2-minute
cadence would put the network on a local pass's critical path, and a swallowed
fetch failure would disable the control it serves). Lanes fetch before cutting a
worktree, so the ref tracks the remote as closely as the fleet pulls; the lag is
bounded by that and is made visible by printing the baseline in the watchdog
line.

### AO-GR-26 — A long-lived loop resolves its own dependencies before taking work

**Origin.** Issue #733 (measured 2026-09-14: the fleet could not spawn a single
subagent).

**Rule.** A loop **preflights its runner and required binaries at startup and on
every respawn**, before reading its queue. If a dependency is unresolvable it
prints one actionable line naming what is missing and where it looked, escalates
**once**, and **holds the queue** — it never fails per item.

**Why.** The runner was resolved from an inherited PATH the loop did not control
(cron's minimal PATH omitted `~/.local/bin`), so every dispatch died with
`FileNotFoundError: 'claude'` — **a fleet that could not spawn a single
subagent** while appearing to run. Because the failure was per-directive, it also
became an escalate-storm: the same defect, multiplied by queue depth.

**Verify.**
- The runner is resolved in code from an explicit, documented search path; the
  launcher passes an explicit environment rather than relying on ambient PATH.
- A preflight failure yields exactly **one** escalation plus a held queue; a
  negative control removes the runner and proves the preflight fires once.

### AO-GR-27 — A loop honours the signals it is sent, and documents the rest

**Origin.** Issue #733 (measured 2026-09-14: asked to `HUP` a loop that does not
handle `SIGHUP`).

**Rule.** Every long-lived loop installs handlers for the signals an operator is
told to use. A signal the loop does **not** handle must be documented as
unhandled — and the documented restart signal must be one that performs a
**clean** stop (release claims, take the in-flight child down). Operators must
never be advised to send a signal whose default action is an abrupt kill.

**Why.** Both loops handle only `SIGTERM`/`SIGINT` (`fleet/terminal.py:1036`,
`fleet/brain.py:262`); `SIGHUP` is unhandled, so its default action
**terminates the process immediately** — bypassing `handle_stop`, the claim
release, and the child teardown. An operator restarting the fleet "cleanly"
would silently corrupt the evidence chain, and the recurring
"can't you just HUP it?" is a documentation defect, not an operator error.

**Verify.**
- The signal set each loop installs is declared and asserted; a check fails when
  a restart signal is recommended that the loop does not handle.
- The runbook names the clean restart signal (`SIGTERM`) and states plainly that
  `SIGHUP` is unhandled and therefore destructive.

---

### AO-GR-28 — Every governed artifact is tagged, and a tag set derives its gates

**Origin.** Issue #1175 (the tag authority) and issue #1183 (making it
constitutional). Measured 2026-09-17: classification existed in five places —
the conformance policy, the surface policy, the FinOps policy, the fleet
vocabulary and the issue forms — and **none of them could say what a tag
implies**. A lane could write `class:elite` and nothing connected that word to a
gate, a FinOps floor, or a lifecycle stage.

**Rule.** One declared vocabulary (`governance/tagging/taxonomy.yaml`)
classifies every governed artifact — issue, PR, branch, commit, surface,
release. The vocabulary **borrows** wherever this repository already has an
authority and never re-declares one: the `class` ladder is borrowed from
`governance/conformance/policy.yaml` and the FinOps tiers from
`governance/finops/policy.json`, and the values are **mirrored and proven
equal** by the gate — a borrow that reads its own values can never fail, so the
mirror is what makes drift detectable. Two dimensions carry the delivery and
lifecycle half: **`posture`** (`overall` | `saas` | `iac` | `no-human-needed` |
`human-gated`) and **`lifecycle`** (`plan` | `build` | `verify` | `release` |
`operate` | `retire`).

A tag set **derives the gates it owes** — by channel (`pr`/`ci`/`cd`/`ops`) and
at the FinOps floor the doctrine sets — so classification has consequences
rather than being a description. Every gate a rule names must **resolve**
(`make:<target>` against the Makefile, `check:<name>` against the check registry
`scripts/verify.sh` builds): renaming a gate fails by name rather than describing
a pipeline that no longer exists. `posture:no-human-needed` and
`posture:human-gated` are **mutually exclusive** and both at once is refused by
name — a contradiction is not a preference, and silently picking a winner is how
a plan becomes a guess.

**Why.** A vocabulary that lives nowhere in particular is a convention, not a
contract: five copies of a classification scheme is five chances for them to
disagree, and no way to notice. The failure this rule prevents is the one
[ADR-0015](decision-records/ADR-0015-routing-seam-single-authority.md) names —
*two things quietly both being authoritative* — and the mechanism is the
repository's own mirrored-constant pattern (`governance/vocabulary/fleet.yaml` <->
`fleet/channel.py`): declare once, mirror where a consumer must be fast, and let
a gate hold the two equal.

**The mandate is half the rule.** The rule is only constitutional while the
contract documents declare it, so `AGENTS.md`, this spine, `docs/GOVERNANCE.md`,
`docs/EXECUTION-PLAN.md` and `docs/QA-GATE.md` each declare it, and
`scripts/check-tagging.sh`'s `tagging-mandate` check FAILS naming the document
**and** the marker the moment one stops. A rule only prose carries is advice
(AO-GR-4); a rule whose declaration is gated is a rule.

**Verify.**
- `scripts/check-tagging.sh` (in `make verify` by auto-discovery, and `make
tagging`) — the authority's shape, every borrowed vocabulary proven equal to its
authority, every rule's gate name resolved, every document against its frozen
shape, the declared controls against the authority they govern, the generated
matrix's freshness, and every declared refusal provoked by a real mutant with its
clean twin accepted.
- `tagging-mandate` — every marker above present in every contract document,
  provoked by stripping each marker in a scratch copy and requiring the refusal
  by name.
- `python3 governance/tagging/cli.py plan --tag …` — the gates a tag set owes;
  `… board --live` — what the board actually declares.

---

## Enforcement & linkage

Golden rules are the top of the product's policy hierarchy. Below them:

- **Policy-as-code** — rules graduate from prose to platform-enforced gates
  (issue #26 policy-as-code + gate engine; issue #28 guard honesty). A rule in
  this doc is advisory until its gate ships — that gap is stated, never
  implied-enforced.
- **ADRs** — architecture decisions live in
  [`decision-records/`](decision-records/) and ratify *how* rules are met; they
  do not relax rules.
- **Governance** — branch/session/provenance conventions:
  [`GOVERNANCE.md`](GOVERNANCE.md). Dispatch contract:
  [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md).

## Provenance & sources

This spine is **adapted** from, not copied from, the following (full index:
[`CANNIBALIZATION.md`](CANNIBALIZATION.md)):

| Source (repo, path) | What is reused |
|---|---|
| `kushin77/CMR` `GOLDEN-RULES.md` | GR-2/3/4/5/6/7/8/9/10/12 rule shapes — issue tracking, one-lane, IaC + flag-gating, no-secrets, SemVer, AI guardrails, provenance, verify-before-done |
| `kushin77/CMR` `docs/decision-records/` | ADR method + numbering + template (see [`decision-records/README.md`](decision-records/README.md)) |
| `kushin77/CMR` `board/epics/EPIC-05-ai-guardrails.md` | AI guardrails = instruction layer + enforcement layer (AO-GR-9) |
| `kushin77/leaderboard` `docs/reference/RULES.md` | R1 verify-live, R5/R15 independent auditor, R8 platform-enforced, R16 no-formalities, R18 control-plane-never-executes |
| `kushin77/leaderboard` `config/policy.yaml` | rule format: `enforcement` / `check` / `status` declared honestly (precursor to issue #26 policy-as-code) |
| `kushin77/shared-frontend` `docs/GOLDEN-RULES.md` | per-rule Rule/Why/Verify format |
| `kushin77/shared-governance` `GLOBAL_STANDARDS/**` | agent-action / JWT / OIDC / task / telemetry contracts, approval gate, external-LLM egress (feed AO-GR-12..19 and the reserved pillar ADRs) |
