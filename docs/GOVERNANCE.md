# Governance — agent-orchestrator

Lean, enforceable governance for the AI-agent-orchestration control plane.
The goal: every change is **tracked, lane-owned, verified, and reversible**,
and each pillar keeps a stable contract so any agent/product surface can join.

> **Pointers:** agents start at [`../AGENTS.md`](../AGENTS.md); the five-pillar
> architecture is [`ARCHITECTURE.md`](ARCHITECTURE.md); parallel dispatch is
> [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md). This repo is a governed fleet repo;
> where it is silent, `kushin77/CMR` golden rules (GR-1…GR-24) bind.

## 1. Branch rules

| Branch | Rules |
|--------|-------|
| `master` | Default branch. Protected **by convention**: no direct pushes, no force-push. Every change lands via a PR (squash merge). Branch protection as code ships with issue #6. |
| `issue-<n>-<slug>` | Short-lived work branches (e.g. `issue-5-repo-foundation`). Merged and deleted. |
| `issue-<n>` | The canonical **lane** branch minted with a session identity (see §1b). One issue = one lane = one branch. |

One branch per issue; a pushed branch must be backed by an open PR or be
deleted (anti-sprawl).

### 1b. Lane isolation — one session identity per issue (institutional)

A lane is not a directory someone was told to use; it is a **minted identity**
(`governance/isolation/`). `cli.py open --issue <n> --agent <id> --lane <lane>`
mints a unique `session_id`, provisions the lane as its own git worktree on
branch `issue-<n>`, and writes the session's git signature into **that
worktree's own config** (`git config --worktree`) — never the shared repository
config, because a signature in the shared config would let one lane sign another
lane's commits. The signature is deliberately non-human:
`agent-<id> <agent+<id>@agents.invalid>` (RFC 2606 reserved TLD).

Four properties are enforced, each by a check that can fail:

| Property | Broken means |
|----------|--------------|
| The branch names the issue | `issue-<n>` (optionally suffixed) — a lane on any other branch is not linked to its ticket. |
| The signature is the session's and lane-local | Inherited or shared-config signatures make authorship meaningless. |
| Every commit the session authored carries `Refs kushin77/agent-orchestrator#<n>` | The generated history must point back at the ticket — checked per commit, so a later commit cannot repair an earlier untraceable one. |
| The worktree is a linked worktree of the repository | A lane inside the shared checkout is not isolated at all. |

`governance/isolation/cli.py audit --all` re-derives these from the filesystem
and git; `scripts/check-session-isolation.sh` runs the same round trip in
`make verify` and fails on each violation.

### 1c. End-to-end closure — every artifact terminal (institutional)

Isolation (§1b) covers the *open*; closure covers the *close*. A work item closes
**hygienically** only when every artifact it created is terminal, and each of
these is a **closure invariant** with a name, a requirement and a remediation:

| Closure invariant | Broken means |
|---|---|
| The pull request is merged | A verified change that never landed. |
| Green evidence names the verified head commit | "Green" is a claim. A squash merge creates a new commit, so evidence names the tree that was verified, never the merge itself. |
| The source branch is gone | The branch outlived its issue; the local squash-merge leaves it behind while the main checkout holds `master`. |
| No live claim is held | A closed issue still claims a lane — the wedge that blocked re-dispatch. |
| The authorisation directive is consumed | A pending directive re-executes the order the moment the claim frees. |
| The issue closed with real evidence | A summary is not evidence; and a landed change still on the board is not finished. |
| The lane worktree is reclaimed | Stale lanes accumulate and collide. |
| An open, milestoned item declares its labels | The conformance gate cannot hold an item to a rung it never declared. |

`governance/lifecycle/cli.py close` drives them in dependency order, is
idempotent, and reports the remainder rather than a success it cannot evidence;
`governance/lifecycle/cli.py audit` re-derives the whole set offline.
`scripts/check-github-lifecycle.sh` provokes one violation per invariant in
`make verify`. Legacy drift is quarantined by name in
`governance/lifecycle/baseline.json`, honoured only while its tracking issue is
open.

### 1d. Orphan reconciliation — a dead session leaves a clean workspace

Isolation (§1b) opens a lane and closure (§1c) finishes one that completed. This
covers the third case: a lane whose agent **died**. Each session writes a
**heartbeat** under `.fleet/sessions/` while it runs; a beat older than the TTL
(15 minutes) is an orphan, and a fresh beat behind a missing process is reported
as *suspect* — absence alone is not enough to reclaim a lane.

Teardown is three-way and decided by **where the orphan's work lives**:

| Work's location | Action |
|---|---|
| landed on `master` | worktree removed, branch deleted, lane forgotten, claim released |
| only on a remote branch | worktree removed, **remote branch kept** (parked), claim released |
| nowhere else | nothing removed: **shelved**, claim kept, reported every pass |

The third row is deliberate and load-bearing: the worker **never trades
unmerged work for an unlocked issue.** Unlocking there would invite a second lane
to start the same work while the first lane's only copy sits in a worktree nobody
is watching. A shelved lane is re-evaluated on every pass and reclaimed
automatically once its work lands.

`governance/reconcile/cli.py sweep` performs one pass and `watch` runs it as a
daemon; `scripts/check-reconcile.sh` proves every outcome (and the refusal) in
`make verify`.

## 2. Session labels & provenance (AI-originated work)

AI-originated issues, PRs, commits, and doc sections declare their source:

- **Session labels:** `[CLI]` / `[COPILOT]` / `[DESKTOP]` / `[HUMAN]` where a
  channel needs disambiguation.
- **AI-assistance declaration:** every AI-originated PR carries an
  `AI-assistance: <runtime> (<mode>)` note (e.g.
  `AI-assistance: Copilot (Relentless, flash/LOW)`).
- **Commit references (GR-2):** every commit references its issue with
  `Refs kushin77/agent-orchestrator#<n>`; every PR closes one with `Closes #<n>`.
- **Merge order / Gate-changing declaration (issue #1054):** every PR carries
  `Gate-changing: no` or `Gate-changing: yes — <paths>` in its `## Merge order`
  section. `scripts/check-pr-contract.sh` cross-checks the declaration against
  the PR's diff (a `no` that touches a gate script, or a `yes` that touches
  none, is refused by name) so `scripts/pr-queue.sh` can order gate-changing
  PRs last mechanically instead of a reviewer reading the file list. The path
  list lives in `scripts/lib/gate-paths.txt`.
- **Provenance (GR-10):** cannibalized/harvested assets record source (repo,
  path, license) in the cannibalization index (issue #8). `vendor/CMR` is a
  pinned submodule; `.research/` clones are gitignored.

## 3. Review & merge doctrine

- Every change lands via a PR. No self-approval ceremony is required under the
  **owner autonomous-merge mandate (2026-09-07)**: an agent merges its own PR
  **only after** green verification evidence (GR-12). **Never merge failing
  work.** Verification replaces the human reviewer in this fleet.
- Merging without green `make verify` is forbidden in all cases.

## 4. Review checklist (every PR)

- [ ] Branch is `issue-<n>-<slug>` based on latest `master`
- [ ] Only files owned by the issue's lane (`docs/EXECUTION-PLAN.md`)
- [ ] Gate of record: `make verify` passes (output attached as evidence)
- [ ] No secrets (covered by the gate scan), no build artifacts, no `vendor/`
      edits, no `.research/` commits
- [ ] Docs kept in sync (architecture / execution plan / governance)
- [ ] AI-assistance + runtime declared
- [ ] `Closes #<n>` referenced
- [ ] `## Merge order` / `Gate-changing:` declared and matches the diff

## 5. Roadmap & work intake

- All planned work = GitHub issues on this repo's board (EPIC-00 = issue #4
  indexes everything; issue labels encode `phase:N`, `pillar:X`, `priority`,
  `type`).
- Every task — planned or ad-hoc — lands on the board before the work (GR-2 /
  board mandate). A commit without an issue reference is a finding.
- Closed issues are closed **with evidence** (the gate output that proved the
  change).

## 6. Lessons (living)

Operational learnings are captured as they surface in:
- `docs/adr/` — durable architectural decisions (issue #7 seeds the ADR
  process),
- memory notes and issue comments — session/repo memory conventions.

A lesson is written down the moment a change reveals it — never "at the end".

## 7. Standing obligations

1. **Gate of record** — `make verify` is the gate until CI lands (issue #6);
   it is honest (no-false-green).
2. **IaC-only live infra** — every live surface is declared (Terraform/Cloud
   Build); ad-hoc console changes are debt and get promoted or retired.
3. **Secrets** — env/secret-manager only; never commit secrets or pass them
   through argv/history.
4. **Flag-gated OFF by default** — new product surfaces ship invisible until
   deliberately enabled.
5. **FinOps** — dispatch at the cheapest capable tier; declared services carry
   resource limits.

## 8. Chronological issue dispatch (mandatory)

All agents must consume GitHub work in a strict end-to-end sequence, not as a
free-form board queue.

- **Rule 1: no ad hoc board picking.** An agent cannot claim a GitHub issue
  simply because it is visible on the board.
- **Rule 2: dependency order wins.** The next accepted issue is the next step in
  the current milestone / phase / parent-child chain, or a child required to
  close an already-open issue.
- **Rule 3: no kanban drift.** A board item unrelated to the active issue
  chain is not valid work. It must remain unclaimed until the current dependency
  path reaches that point.
- **Rule 4: close the active chain before branching.** A new issue may only be
  picked if it is directly required for closure of the already-open issue or a
  planned continuation of the same execution path.
- **Rule 5: monotonic progress.** Agents must advance from prerequisite → child
  → validation → closeout, not skip ahead to unrelated tasks.
- **Rule 6: the active epic is a chain edge.** A `Parent: #n` edge to the epic
  the fleet is currently driving (`.board/focus.json`, epic focus #707) is a real
  dependency edge: that epic's open children are the next valid steps. This is
  *stricter* than the milestone frontier, never a relaxation — an item with no
  such edge is still refused `no-chain-edge`.
- **Rule 7: out-of-epic work is parked, not dispatched.** While a focus is
  active, an issue outside the active epic is REFUSED `out-of-epic-pooled` even
  when it is otherwise the milestone frontier — a frontier that interleaves
  several epics is the incoherence the focus exists to prevent. The refusal is
  recorded in `.board/pool.jsonl` (`reason: out-of-epic`, timestamped), so
  deferred work is parked rather than dropped. Work named in a `Blocked-by:` of an
  active-epic child is promoted just-in-time through the *existing*
  `claim --directive <id>` path (`brain-directed`); no new authorisation
  mechanism is invented. When the resolver returns `None` (no workable epic) the
  pool drains and reports what left it.

This prevents "issue scavenging" and ensures the board behaves like a governed
execution plan instead of a generic kanban.

### 8.1 Claim-time enforcement (code, not advice)

A rule that only exists in prose is advisory (GR-29), so the dispatch order is
enforced by code:

- **Claim before working.** `python3 governance/dispatch/cli.py claim --issue
  <n> --agent <id> --lane <lane>` validates the issue against the committed
  board snapshot (`.board/snapshot.json`) and writes the claim to the append-only
  ledger (`.board/claims.jsonl`) with the reason it was accepted.
- **Refusal reasons.** `unknown-issue`, `issue-closed`, `blocked`,
  `already-claimed`, `epic-not-workable`, `out-of-epic-pooled` (outside the
  active epic — see rule 7) and `no-chain-edge` (kanban
  scavenging). The last one is the rule in action: a visible board item that is
  not the next step is not work.
- **Brain directives are chain edges.** A claim may carry `--directive <id>`;
  the directive must exist in `.fleet/sent`, be a brain-issued directive, and
  name exactly this issue — the claim is then recorded as `brain-directed`.
  Off-frontier work without a directive remains refused.
- **Epic focus is a chain edge.** A child of the active epic may be claimed with
  the additive reason `active-epic-child`. The audit re-derives the edge — the
  issue must declare a `Parent:` naming an open epic that IS the active epic —
  and reports the claim otherwise, so the reason cannot be abused.
- **Out-of-epic work is pooled.** An out-of-epic refusal appends
  `.board/pool.jsonl`; `governance/dispatch/cli.py pool` shows it and the pool's
  own self-control proves a malformed line is rejected and a drain reports what
  it drained (a silent drop is the failure the pool exists to prevent).
- **Single claim.** An in-flight issue is locked; a second agent's claim fails
  loudly. A claim whose TTL elapsed may be taken over, so a dead agent cannot
  wedge the chain.
- **Gate.** `make issue-claims` (part of `make verify` and `make lint`) replays
  the ledger against the snapshot and fails on any violation. The audit runs its
  own mutants first, so it cannot pass vacuously.

See `governance/dispatch/README.md` for the reason table, the chain markers
(`Parent: #n`, `Blocked-by: #n`) and the CLI.

## 9. Spine coverage map (issue #890)

Every rule id in the spine — `AO-GR-1`..`AO-GR-27` in `docs/GOLDEN-RULES.md`,
plus the hub (`kushin77/CMR`) rules this repo's own doctrine cites by number
(`GR-2`, `GR-3`, `GR-4`, `GR-5`, `GR-6`, `GR-7`, `GR-8`, `GR-9`, `GR-10`,
`GR-12`, `GR-17`, `GR-20`, `GR-22` in `AGENTS.md`/`docs/GOLDEN-RULES.md`) —
maps to a named control in [`governance/controls/spine-coverage.yaml`](../governance/controls/spine-coverage.yaml).

This is deliberately a **superset** of `scripts/control-coverage.tsv` (#874),
which maps only Part B (`AO-GR-12`..`AO-GR-20`) of the spine and remains the
row-level source of truth `scripts/check-control-coverage.sh` validates for
that range. `governance/controls/spine-coverage.yaml` gives the same
treatment to Part A (`AO-GR-1`..`AO-GR-11`), Part C (`AO-GR-21`..`AO-GR-27`),
and the hub rules — so a reviewer can walk from **any** rule id in either
document to the control that enforces it, and a control silently dropped from
either map is a gate failure, not a missed grep.

### Status vocabulary

The honest three, never a plain pass/fail:

| Status | Meaning |
|--------|---------|
| `ENFORCED` | A gate control exists, the file it names is present and invokable, and (for a suite) `scripts/pytest-suites.txt` declares it. |
| `PARTIAL` | A control exists and runs, but its own row records — in plain language — the part of the rule it does *not* cover. |
| `GAP` | No control enforces the rule today. Recorded by rule id, never silently dropped or rounded up. |
| `not-applicable` | A hub rule id that appears in this repo's text only as a range endpoint (e.g. "`GR-1..GR-24`") and is never cited for a specific local decision — out of local scope; the hub repo owns its control. |

Five rules are currently recorded `GAP` (as of #890): `AO-GR-10`
(cannibalization-index gate), `AO-GR-23` (its `Verify.` block names
`scripts/check-lane-stranded.sh`, which does not exist in the repository —
recorded as a missing-gate GAP, not rounded up), `GR-10`, `GR-17`, and
`GR-22`. These are real gaps, not citation misses; closing each is a
follow-up issue, not silently patched into this map.

### How to add a control

1. Find (or write) the `scripts/check-*.sh` / `scripts/check-*.py` gate that
   enforces the rule, or the pytest suite directory that does — and if it's a
   suite, declare it in `scripts/pytest-suites.txt` so
   `scripts/run-pytest-suites.sh` actually runs it.
2. Add or update the rule's row under `rules:` in
   `governance/controls/spine-coverage.yaml`: `status`, `covered`, `origin`
   (`local` / `hub-cited` / `hub-uncited`), `modules` (the code paths the
   control covers), `gate` (comma-separated control files, or `suite:<dir>`),
   and a `note` if the coverage is partial.
3. Run `bash scripts/check-spine-coverage.sh` — it parses every rule id out
   of `AGENTS.md`/`docs/GOLDEN-RULES.md`, requires exactly one row per id,
   requires every named gate file to exist and be invokable, requires every
   `suite:` gate to be declared, and refuses any row claiming `covered: true`
   without a real gate. It runs its own negative control (a mutated copy
   pointed at a nonexistent gate must be refused **by name**) via
   `governance/controls/check_spine_coverage.py --self-test`.
4. Run `python3 -m pytest governance/controls -q`
   (`governance/controls/tests/test_spine_coverage.py`) before opening a PR.

### Delivery controls (#803)

#803 (branch protection, required checks, CODEOWNERS as delivery controls) is
an open EPIC this map does not close. The `delivery_controls:` block in
`governance/controls/spine-coverage.yaml` records the controls #803 is about
as **present** in this repo today (`scripts/check-branch-protection.sh`,
`scripts/check-gate-status.sh` + `scripts/check-gate-coverage.sh`,
`scripts/check-codeowners.sh`) — the residual #803 tracks is making
GitHub-side branch protection itself IaC-declared (issue #6 / PR #1075), not
the absence of a local check.

## 9. The tag authority (mandatory)

Every governed artifact — issue, PR, branch, commit, surface, release — is
classified against one declared vocabulary: the **tag authority** at
`governance/tagging/taxonomy.yaml` (AO-GR-28, issues #1175 + #1183). This section
is the governance half of that rule; the mechanism is
`scripts/check-tagging.sh`, which runs inside `make verify`.

**One authority, borrowed from — never a second copy.** The `class` ladder is
borrowed from `governance/conformance/policy.yaml` and the FinOps tiers from
`governance/finops/policy.json`. Those values are **mirrored in the taxonomy and
proven equal to their authority by the gate**, because a borrow that reads its
own values can never fail: the mirror is what makes drift detectable. A lane may
not mint a rung, a tier or a role by editing one side.

**The dimensions.** Board classification (`class`, `type`, `priority`, `area`,
`pillar`, `phase`, `gdc`, `source`, `epic`) is joined by two this authority adds:

| Dimension | Values | What it decides |
|---|---|---|
| `posture` | `overall`, `saas`, `iac`, `no-human-needed`, `human-gated` (multi-valued) | which delivery gates apply: `iac` owes declared-infrastructure plus **flag-gated-OFF**; `saas` owes the identity and boundary evidence; `no-human-needed` must finish with **no operator input at all** and forbids escalation markers; `human-gated` owes the pre-merge contract |
| `lifecycle` | `plan`, `build`, `verify`, `release`, `operate`, `retire` | which half of the pipeline applies — the SDLC stage, and therefore the `pr`/`ci`/`cd`/`ops` channel a gate runs in |

`posture:no-human-needed` and `posture:human-gated` are **mutually exclusive**
and both at once is refused by name: a contradiction is not a preference, and
silently picking a winner is how a plan becomes a guess.

**A tag set derives the gates it owes.** `governance/tagging/rules.yaml` maps a
tag set to gates by channel, at the FinOps floor the doctrine sets — every gate
named as `make:<target>` or `check:<name>`, and every name **resolved** against
the Makefile and the check registry `scripts/verify.sh` builds. Rename a gate and
the tagging gate fails by name rather than describing a pipeline that no longer
exists. Ask for the plan with:

```bash
python3 governance/tagging/cli.py plan --tag class:elite --tag posture:iac --tag lifecycle:release
```

**Calibration.** `class`, `type`, `priority` and `area` are **required**;
`posture` and `lifecycle` are **recommended**, reported as deviations and
escalated only by `--strict`, because the board predates them. Enforcing a
brand-new dimension against a legacy board would paint the gate red for a reason
no lane can fix by working its own issue. The prevention half — the filing path
deriving both dimensions so no NEW issue is born unclassified — is issue #1182.

**This section is enforced, not advisory.** `tagging-mandate` FAILS naming the
document and the marker the moment `AGENTS.md`, `docs/GOLDEN-RULES.md`, this
file, `docs/EXECUTION-PLAN.md` or `docs/QA-GATE.md` stops declaring the rule
(GR-29: a rule only prose carries is advice).
