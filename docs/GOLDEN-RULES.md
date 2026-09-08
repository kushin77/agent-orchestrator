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
