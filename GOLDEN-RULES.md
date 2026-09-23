# Golden Rules — CMR hub

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 9d61bf5

Binding for **this repository and every repository CMR governs**. Where a golden
rule and a policy or doc disagree, the golden rule wins. Rules are terse on
purpose; each exists because its absence has already caused (or would cause) a
recorded failure in the `kushin77` fleet. See `board/INTENT.md` for the R#s each
rule anchors to, and `AGENTS.md` for how agents work here.

---

## GR-1 — The hub holds standards & machinery, never spoke application code

NG4. CMR hosts manifests, governance, templates, policy, and registry indexes
only — never live app code, never submodules/subtrees of apps. (R1)

## GR-2 — Everything is tracked in a GitHub issue

Opened **before** the work, updated during it, closed with evidence. No
exceptions for "small" changes. A commit without an issue is work nobody can
find later across ~100 repos.

**Typed cross-referencing (XR-004).** Every reference is typed so a crawler can
follow it without parsing prose:
- Commits reference the issue: `Refs <owner>/<repo>#<n>` — same-repo
  `Refs kushin77/CMR#249`, cross-repo `Refs kushin77/ERP-CRM#42`.
- PRs close the issue: `Closes #<n>` (same-repo `Closes #249`; cross-repo
  `Closes <owner>/<repo>#<n>`).
- Docs link with permalinks: `owner/repo@<sha>/<path>#Lx-Ly` (e.g.
  `kushin77/CMR@5341f46/docs/BRANCH_GOVERNANCE.md#L40-L52`).

A commit without `Refs` (or an issue number) is a conformance finding (XR-007).

## GR-3 — One issue = one lane; no two lanes share a file

Work only files your issue owns (see `fleet/MANIFEST.tsv` / `docs/EXECUTION-PLAN.md`).
Smallest focused diff; no unrelated edits. (R13) Operating layer:
`guardrails/instructions/agent-operating-doctrine.md`.

## GR-4 — `main` is protected; every change lands via a reviewed PR

PR required, required status checks, no direct pushes (enforced via Terraform,
CMR-104). **No auto-merge; merging without green CI is forbidden.** (NG2)
Whether a *self-authored* PR may be merged is tripwire-conditional — see the
canonical merge rule below, which governs. A
denied action (e.g. a blocked push) is never routed around via another tool
or agent: `guardrails/instructions/agent-operating-doctrine.md`. Solo-dev
self-merge carve-out: ADR-0027; `git push` solo-dev carve-out: ADR-0020; both
are part of the single umbrella posture in
`docs/decision-records/ADR-0030-solo-dev-operating-posture.md`, all gated on
the same `ops/scale-tripwire.sh` reinstatement trigger.

**Canonical merge rule (single source of truth — all other docs point here):**

*Resolution order (so two blocks can never silently disagree again):* this
block is the operative merge rule. Where any other text in this repo, in
`onboarding/**`, or in a rendered spoke instruction file appears to state a
merge rule, **this block and the ADRs it cites govern**; the other text is
stale and is a conformance finding, not an alternative reading.

**Always absolute, at every scale — never lifted by any carve-out:**
- **Merging without green CI is forbidden.** This is an *evidence* gate, not
  a coordination gate: no tripwire state, ownership, or authorship makes it
  optional. GR-12 verification evidence must be attached.
- **No auto-merge** (NG2) — a merge is always a deliberate, evidenced act.
- Branch protection, required status checks, and no direct pushes to `main`
  remain Terraform-sourced (CMR-104, GR-5/GR-15).

**Independently-authored PRs.** An agent may merge a PR it did **not** author
— a bot-authored PR (e.g. a Dependabot bump) or a controller standards-bundle
PR in an `auto_merge=true` repo — when the absolutes above are satisfied.
This is not a self-merge. **Authorship is the GitHub PR author (the
account/identity that opened the PR), not the commit-author trailer or which
agent lane/session wrote the diff** — a cosmetic trailer (e.g. "Akushnir
Agent") does not make a PR independently-authored.

**Self-authored PRs — tripwire-conditional (ADR-0027, ADR-0020, umbrella
ADR-0030; amended by ADR-0033):**
- **While `ops/scale-tripwire.sh` has NOT fired:** a CMR agent may merge a PR
  it authored on **CMR itself**, provided the absolutes above hold and the
  work is the active owner's own solo-authored work. A single-account repo
  does **not** disqualify this carve-out — the solo-account condition is
  precisely the state the carve-out exists to serve.
- **Once the tripwire fires:** the carve-out reverts instantly and a
  self-authored PR requires an independent human reviewer's approval before
  merge (NG2 rubber-stamp rule).

**Scope — this carve-out is CMR-hub-scoped.** It authorizes a CMR agent to
self-merge *on CMR*. Merging inside a spoke/vendor repo is governed
separately by GR-14 + ADR-0030's four gates (scale, **ownership** — a
kushin77-owned repo registered in `channels/spokes.tsv`, evidence, and a
direction issue). Instruction text rendered *into* a spoke addresses that
spoke's own agents and does **not** inherit this hub carve-out; for a repo
that fails ADR-0030's ownership gate, "never self-merge/self-approve" remains
absolute. (GR-4/NG2 · #242-C6 · ADR-0033)

## GR-5 — Everything is declared in IaC; no console clicks

Repo governance is Terraform (GitHub provider); infra changes are
PR → `terraform plan` → code-native runner `apply` (local/on-prem/GCP — GitHub
Actions is disabled). New infra ships **flag-gated OFF** by default. Never ad-hoc
`terraform apply`. (R2)

Enforcement points: `make tf-flag-gate` (`scripts/tf-flag-gate.sh`, in the default
`make verify` chain) mechanically rejects any Terraform master flag-gate variable
that does not default to `false`. `make tf-drift` (`scripts/tf-drift-check.sh`,
read-only `terraform plan`, never `apply`) checks declared config against live
GitHub state for console-click drift when a `GITHUB_TOKEN` and remote backend are
configured; it honestly `SKIP`s (not a pass) otherwise — see
`docs/ENFORCEMENT-GAP-ANALYSIS.md` GR-5 row.

## GR-6 — No secrets in code, files, or git history

Env / GSM / secret managers only. Never commit a real token or key; gitleaks
runs in verify. (R12)

## GR-7 — SemVer is law; consumers pin

`vX.Y.Z` git tag = source of truth. Nothing force-pushes breaking changes
downstream; upgrades arrive as reviewable PRs. (R7, NG2)

## GR-8 — Private by default, forever

Distribution is always private; consumption is restricted, opt-in, and audited.
Anything public would be a separate SaaS product. (R4, NG3)

## GR-9 — AI guardrails are REQUIRED in every governed repo

Every repo CMR governs ships AI instruction files — the canonical `AGENTS.md` plus one
thin pointer file per agent runtime (the runtime → file map lives in
`docs/MODEL-AGNOSTIC.md`) — so agents inherit architecture, security,
and standards; AI output clears the same gates as human output. (R11, R12)

## GR-10 — Provenance is mandatory for canibalized assets

Every harvested asset records its source (repo, path, license) in
`canibalization/INDEX.md` / `registry.csv` before reuse. (EPIC-06)

When CMR vendors a module — pins a module's code into CMR's own tree (e.g.
`catalog/indexer/` vendoring `kushin77/code-indexing`) — onboarding and every
pin-bump MUST include a full-repo review of the vendor's source, not just the
feature being pinned, to identify further real assets worth harvesting for
that module's own stated purpose in CMR. This widens what is *reviewed*, not
what may be *copied*: harvests stay pattern-level reimplementation with
license recorded, same as every existing row (never verbatim, never whole-app,
GR-13/NG4). "Reviewed at `<sha>`, nothing further worth harvesting" is a
valid, recordable outcome — the rule mandates the review and its recorded
outcome, not a harvest quota. Findings are recorded as new `candidate` rows in
`registry.csv` for human/agent triage (never auto-harvested). Distinct from
GR-23: GR-23 mechanically diffs a *consumer's* local divergence back to
upstream; this reviews the *vendor's whole repo* outward into CMR. Machine:
`scripts/harvest-scan.sh` (bounded tree-listing + heuristic classification
against the module's own `module.json`, not a line-by-line review).

## GR-11 — Shared-module changes pass a blast-radius gate before republish

Any change to a consumed module runs dependents' integration tests; fixes and
lessons always have a path back to the hub. (R8, R10)

## GR-12 — Verify before claiming done

"Done" requires evidence: the issue's `Verify:` command and `make verify`, with
actual output. A gate that cannot fail is a formality and will be rejected.
Full "resolved with evidence" definition:
`guardrails/instructions/agent-operating-doctrine.md`.

Sibling clause — GR-32: a check that *does* fail and is never heard is a
formality too (an `except …: pass` between the failure and the report is
indistinguishable from a pass).

## GR-13 — Consumers choose features à-la-carte

Modules are decomposed; consumers pin and toggle individual features, and
consume only what they need — never whole-app vendoring. (R6, R9)

## GR-14 — CMR develops only CMR; vendor work is directed, never done

CMR agents never write code/files in a vendor, module, or spoke repo. Anything a
vendor needs to do lands as a **direction issue** on the vendor's own board; anything a
vendor needs from CMR lands on the CMR board. Bidirectional handoff defaults to the two
boards (`docs/VENDOR-HANDSHAKE.md`). (NG6)

**Solo-dev carve-out (`docs/decision-records/ADR-0030-solo-dev-operating-posture.md`):**
while `ops/scale-tripwire.sh` has not fired, a CMR agent may write directly to and
self-merge in a kushin77-owned, onboarded (`channels/spokes.tsv`) spoke repo in place of
filing-and-waiting, provided the spoke's own CI/verify gate is green and a direction
issue documenting the change (what, why, evidence, PR link, ADR-0030) is filed on the
spoke's board as part of the same piece of work. Branch-protection/repo-governance
settings changes are **not** covered by this carve-out at any scale — those stay
Terraform-sourced (`infra/terraform/github/**`, GR-5/GR-15). The carve-out reverts the
instant the tripwire fires.

## GR-15 — No GitHub Actions; automation is code-native

GitHub Actions is **disabled** on CMR and every governed repo (Terraform:
`infra/terraform/github/actions.tf`). All automation — verify gate, outbound/inbound
channel relay, domain-controller takeover, drift inventory — runs **code-native**
(local / on-prem / GCP: cron · systemd · Cloud Scheduler · Cloud Build) via `ops/run.sh`
and the scripts it wraps. No `.github/workflows/` files.

Decision ratified and recorded: `ADR-0011` (2026-09-06).

## GR-16 — Every push to `main` notifies each onboarded vendor

Every successful merge/commit/push to CMR `main` notifies each `onboarded=true`
vendor with a **pinned notification issue** on the vendor's own board
(`cmr:notify` label, one issue per push) summarizing the hotfixes/versions/enhancements
in the push — vendors are never left guessing. Notification is channel machinery
(`channels/notify.sh`, `ops/run.sh notify-vendors`, `.githooks/post-commit` queue):
it files an issue on the vendor's board via `gh`, never into the vendor's repo
(GR-14), and it never merges or approves anything (GR-4, NG2).

## GR-17 — Debug local-code-first; the indexer KB answers the org-wide question

When debugging, search the repo's own code first — the local-code-first pass
(grep / usage search / tests) precedes any other step. For cross-repo or
org-wide knowledge, query the CMR indexer KB (MCP) instead of guessing or
re-cloning. Skipping the local pass or ignoring the indexer is the same
anti-pattern. (R11, EPIC-10 / CMR-1006)

## GR-18 — Mandatory default modules ship to every repo CMR touches

Modules flagged `mandatory` in the catalog (e.g. the CMR indexer KB) ship to
EVERY repo CMR touches — no questions asked, no opt-out. Changes to the
mandatory set are a standards-package change and propagate like standards
(`docs/MANDATORY-MODULES.md`, `controller/standards-sync.sh`). (GR-17 pairing,
EPIC-10 / CMR-1006)

## GR-19 — Full GitHub hygiene on every governed surface

No stale open PRs, branches, worktrees, Dependabot PRs, error rows, or alert
rollups on any governed surface; local/remote drift is reconciled; `ops/run.sh
hygiene` runs on the loop and fails loudly (a red gate blocks the next dispatch
wave); repeated failures get an RCA. The scanner is read-only — it reports and
names remediation commands, never merges or closes anything remote (GR-4/NG2,
GR-14/NG6). Mandate: `docs/HYGIENE.md`; engine: `ops/hygiene.sh`;
template: `docs/RCA-TEMPLATE.md`.

## GR-20 — Board mandate: every task lands on the CMR issues board

Every task — planned or ad-hoc, every lesson, enhancement, catch, and fix — lands
on the CMR issues board; work done off-board is backfilled onto the board
(retroactively if needed) so the meta/KB learns from it. No delta stays out of
the board. Policy: `docs/BOARD-MANDATE.md` (GR-19 pairing — pin hygiene: a closed
pinned issue is pollution).

## GR-21 — Ephemeral-only localhost mechanics (no persistence)

Agents may use any localhost mechanical process to finish a task with no human
(local scripts, `curl localhost`, local servers, temp files) — but every
localhost artifact (temp files, servers, ports) MUST be ephemeral: created
under a temp dir, killed/removed on exit; nothing persists; no daemons survive
the task. Prefer the sandbox helper `controller/ephemeral.sh`; policy:
`docs/EPHEMERAL.md`; instruction layer:
`guardrails/instructions/ephemeral-localhost.md`.

## GR-22 — No-questions doctrine: apply the defaults, never ask

Agents apply the documented defaults and proceed autonomously (IaC/code-native,
no-human-needed). Never ask the user for clarification unless a golden rule
explicitly requires human action (secrets, `terraform apply`, merge/approve) or
a genuinely irreversible, safety-critical choice has no documented default.
Every default lives in `docs/DEFAULTS.md`; a missing default is a bug to record
on the board, not a question. Instruction layer:
`guardrails/instructions/no-questions.md`; gate: `scripts/check-no-questions.sh`.
Operating layer (orchestration, lanes, FinOps tier, evidence, escalation):
`guardrails/instructions/agent-operating-doctrine.md`.

---

## GR-23 — Consumer feedback is mandatory: modules stay elite by closing it

Every consumer that vendors a module and completes the process end-to-end MUST
emit a **feedback direction** to the module vendor — the lessons learned (issues
hit and resolved) plus **suggested enhancements** to fix upstream. The module
vendor MUST resolve each enhancement and push the fix upstream (PR-back, GR-11,
blast-radius-tested) so every consumer — not just the one that hit the issue —
inherits it. Hand-typed capture is not sufficient on its own: when a consumer's vendored
copy of a module actually **diverges** from its pinned upstream version
(git-tag pin, `docs/DISTRIBUTION.md`), that divergence — the real diff/patch,
not a prose summary — MUST be captured as evidence. Once triage (human or the
module vendor) confirms a captured divergence is a genuine bug fix or working
enhancement rather than an intentional fork/harvest scope, submitting it
upstream as a hotfix PR-back is mandatory, so every consumer inherits the fix
once resolved (tracked as a `docs/LESSONS.md` LESSON-*/SUGGEST-* row). Machine:
`sync/vendor-feedback.sh` (`ops/run.sh vendor-feedback`) — `capture` for
hand-typed lessons, `diff-capture` to mechanically detect and record a
consumer's real divergence as evidence (flagging broad, harvest-shaped
divergence as informational rather than an automatic mandate); both feed
`file`, which consolidates them into one direction issue carrying
`cmr:feedback` + `cmr:direction`, the attached patch evidence, and the
completion contract (`CMR:DONE`), tracked by the vendor-progress poller.
Doctrine: `docs/VENDOR-FEEDBACK.md`.

---

## GR-25 — Governance/policy issues carry a literal close checklist

Every `area:governance` / `area:policy` ("enterprise policy") issue MUST NOT be
closed — by a human or an agent — without a completed **Definition of Done —
Enterprise Policy Close Checklist**: which `docs/SOLUTION-CLASSES.md` tier the
fix landed at, whether it's IaC-backed (GR-5), whether it reduces "no human
needed" steps (GR-22), whether an RCA was written per `docs/RCA-TEMPLATE.md`
(if incident-class), and a SaaS/multi-tenant applicability note. The checklist
lives in the issue templates (`.github/ISSUE_TEMPLATE/*.yml`); the binding
rule for agents is `guardrails/instructions/enterprise-close-checklist.md`.

---

## GR-24 — The PR queue stays clear: checkable thresholds, not sentiment

An open PR is a liability, not a badge of throughput. The queue MUST stay
inside concrete, machine-checked limits — enforced by `scripts/verify.sh
queue-hygiene` (`scripts/check-queue-hygiene.sh`), not aspiration. This is
the pilot instance of CMR's **controls registry** model (GR-5 generalized):
every control is coded, defaults `enabled: false`, and is declared as one
row in `guardrails/policy/controls.yaml` (schema: `controls.schema.json`;
validator: `controls-policy.sh`, which fails loudly if a registered
control's script is missing or a real gate isn't registered). The registry
is the thing a production web toggle will eventually flip directly — today
it's edited by hand and read at gate-run time; `scripts/check-queue-hygiene.sh`
reads its own `enabled` flags and `thresholds.*` straight out of that file
(falling back to its old hardcoded defaults only if the registry is
unreadable, and never for the one control below that must stay on):

- **Max open PRs** (`thresholds.max_open_prs`, registry default 10 — row
  `queue-depth-limit`, `mode: warn`, `enabled: false`). Above this, dispatch
  is outrunning review/merge capacity — the fix is to stop dispatching, not
  to widen the limit (see the WIP gate this rule requires, below). Off by
  default: reported, not blocking, until the owner flips it on.
- **Max open age per PR** (`thresholds.max_age_hours`, registry default
  48h — row `queue-age-limit`, `mode: warn`, `enabled: false`). A PR idle
  past its threshold is either abandoned, blocked on something undocumented,
  or (as below) permanently unmergeable — every case is a defect to surface
  immediately, not a queue item to wait out. Off by default, same as above.
- **The merge path must be provably satisfiable** (row
  `unsatisfiable-merge-gate-detection`, `mode: block`, **`enabled: true`** —
  the one row in this registry that ships ON, because it costs nothing to
  run and catches a class of bug that silently deadlocks the whole repo). A
  PR that is `MERGEABLE` but `BLOCKED` with **no possible approver** (e.g. a
  required CODEOWNER review that only the PR's own author can give) is not
  "waiting for review" — it can never merge by construction. The gate MUST
  detect and loudly report this condition as a distinct, named failure,
  separate from the count/age checks, because an unsatisfiable merge gate is
  exactly the failure mode that produced the 2026-09-07 27-PR backlog
  (ruleset `main-protection` requiring CODEOWNER approval from `* @kushin77`
  while every PR is authored by `kushin77` — self-approval is impossible).
- **Dispatch stops when the queue is over threshold.** A WIP limit on the
  dispatch side (see `docs/EXECUTION-PLAN.md` / `fleet/dispatch.sh`) MUST
  refuse to open new lanes while GR-24's PR-count threshold is exceeded.
  Fast dispatch with no work-in-progress limit is a contributing cause, not
  a coincidence.
- The gate FAILS LOUDLY (non-zero exit) when it cannot run at all — no
  network, no `gh`, not authenticated — never a silent SKIP/exit 0 (per the
  documented anti-pattern of gates that pass while doing nothing).

Machine: `scripts/check-queue-hygiene.sh`, wired via `scripts/verify.sh
queue-hygiene`. Advisory locally (network-dependent); blocking in the
code-native runner (docs/EXECUTION-PLAN.md's PR #350 runner) where `gh`
network access is guaranteed. Ruleset changes that would resolve an
unsatisfiable-merge finding are Terraform-only (infra/terraform/github) and
remain the owner's call — this rule mandates detection and reporting, not
unilateral ruleset edits.

## GR-26 — Rebase drift (env vars, fixtures, generated artifacts) is a
blocking pre-push gate, not a post-merge surprise

A branch authored correctly against the `main` it forked from can still be
wrong at merge time if `main` moved underneath it in a drift-prone area:
`docs/ENVIRONMENT.md` gained a `CMR_` var registration the branch never
knew about, `guardrails/gates/corpus/corpus.json` fixtures now assume a
signal the branch didn't ship, or a generated artifact (`portal/assets/cmr-state.js`
via `portal/scripts/gen-state.py`) went stale. Each individual check already
existed (`scripts/check-env.sh`, `scripts/gate-corpus.sh`, `gen-state.py
--check`) and already ran inside `make verify` — the gap was that nothing
*forced* `make verify` to run automatically at the moment the drift is
introduced (right after a rebase/merge, before the branch reaches a PR).

- `make rebase-check` runs the drift-prone subset (env-lint + gate-corpus +
  the DR-076 portal staleness check) as one named target.
- `.githooks/pre-push` runs `make rebase-check` and **blocks the push**
  (non-zero exit) if it fails — the last local checkpoint before a branch
  reaches a PR. `CMR_SKIP_REBASE_CHECK=1` is the explicit, loud opt-out
  (never a silent skip).
- `.githooks/post-merge` and `.githooks/post-rewrite` run the same check
  immediately after a merge/rebase and print a loud (non-blocking) warning,
  so the drift is visible at the moment it's introduced, not only when the
  push is later rejected.

Machine: `make rebase-check` (Makefile), `.githooks/pre-push`,
`.githooks/post-merge`, `.githooks/post-rewrite`. Self-test:
`make rebase-check-self-test` (`tests/rebase-check-test.sh`), wired into
`make verify` (GR-12: proves the gate can fail — both the underlying
`check-env.sh` fixture rejection and the `pre-push` blocking wire-up are
exercised against planted failures).

---

## GR-27 — Mechanical persistence routes to the shared platform, never ad hoc

GR-21 forbids persistent localhost mechanics; this rule says where the need goes
instead. Any mechanical need requiring real persistence — scheduling, secrets
storage, caches, durable state, cross-run observability — MUST route to the
fleet's shared-services platform once it is a registered CMR module
(`kushin77/CMR#442`), never to an ad hoc local, one-off, or per-repo solution.
Until that registration lands, the need is recorded against its board issue
instead (the standing case: `kushin77/CMR#578`, the uninstalled `adopt` cron) —
recording is mandatory; building ad hoc infra is not an option in either state.
Vendor direction: `CMR:ONBOARD-0010` (`kushin77/CMR#585`). Instruction layer:
`guardrails/instructions/mechanical-persistence.md`.

---

## GR-28 — A standards-bundle change gets a PR opened on every onboarded spoke ASAP

CMR can only guarantee the hub-side half of propagation: **a PR opened**, not a
PR merged (`kushin77/CMR#461` draws this line explicitly — whether a spoke
merges is that repo owner's decision, never CMR's to promise). Binding only
what CMR can actually deliver:

- Any agent whose commit changes a file tracked in
  `controller/standards-manifest.txt` or `sync/gdc-policy-bundle.txt` MUST,
  before treating that commit as "done" (GR-12), invoke the propagator itself:
  `bash controller/standards-sync.sh --kind standards --apply` (or `--kind
  policy --apply` for the policy bundle), scoped with `--repo <owner/name>`
  when only one spoke is affected. This is the *today* mechanism, because no
  unattended trigger exists yet: `sync/gdc-feed.sh` is never invoked by
  anything automatically — no cron, no GitHub Actions (disabled, GR-15), no
  webhook receiver (`kushin77/CMR#578`, `kushin77/CMR#582`). Bare
  `ops/run.sh propagate` / `standards-sync.sh` with no `--apply` is plan-only
  (GR-5) and satisfies nothing under this rule.
- Once an unattended trigger lands (`kushin77/CMR#578`/`#582`), it takes over
  this obligation and the manual invocation above becomes redundant, not
  wrong — the rule binds the outcome (PR opened ASAP), not the mechanism.
- `sync_one()`'s `--apply` only ever opens a `cmr/standards-sync` /
  `cmr/policy-sync` PR; it force-pushes that branch and calls `gh pr merge
  --auto` *only* when the target repo's own `channels/spokes.tsv` col 8
  (`auto_merge`) is `true` — this rule never authorizes pushing to a spoke's
  `main` or merging on its behalf (GR-4/NG2). The `guard` column (col 5) is a
  separate concern — it gates direction-issue auto-filing in
  `channels/send.sh`/`ticket.sh`, not standards-PR delivery — and is
  deliberately not read by `targets()` (`kushin77/CMR#452`).
- **Not yet true, and this rule does not claim otherwise:** an "N-0" SLA
  (repos provably current at all times) remains unearned until
  `kushin77/CMR#461` closes, which itself is blocked on the unattended
  trigger landing and running at least once in anger. Do not read a PR opened
  under this rule as evidence of N-0.
- **Enforcement:** no mechanical gate exists yet to catch a bundle-touching
  commit that skipped this step — tracked at `kushin77/CMR#450`. Until one is
  built (e.g. a post-commit check mirroring GR-16's queue pattern), this rule
  is DECLARED-ONLY and relies on the committing agent's own GR-12
  verification pass.

---

## GR-29 — A doc-only rule is advisory; only a platform-enforced gate is binding

Writing a rule down changes nothing for an agent that never reads it, or reads it
and proceeds anyway under pressure. GR-12 already says "a gate that cannot fail
is a formality"; this rule states the sharper, general form: **a rule that lives
only in prose is advisory, no matter how emphatically it is worded — it becomes
binding only when a platform mechanism can actually block the violating action**
(a branch-protection rule, a pre-commit/pre-push hook that exits non-zero, a
required CI/status check, a schema/validator that rejects bad input). The
general pattern this guards against: a doc-only rule gets violated, gets
re-documented more emphatically, and gets violated again — sometimes repeatedly
in the same day — until an actual gate replaces the prose. Doc discipline alone
does not stop an agent that doesn't read the doc.

- Before shipping a rule as "the fix" for a recurring violation, ask: **what
  stops this if the doc is never read?** If the honest answer is "nothing,"
  the rule is not done — pair it with a gate, or explicitly record the gate as
  outstanding work (never silently ship the doc alone and call it closed).
- A rule may legitimately ship doc-first when the gate isn't buildable yet
  (see GR-28's "DECLARED-ONLY" pattern) — but the doc must say so plainly, and
  the missing gate must be tracked as its own issue, not left implicit.
- This does not relax GR-12 (evidence required for "done") or GR-20 (every
  task lands on the board) — it adds the mechanism question to both: evidence
  of a doc existing is not evidence the rule is enforced.

---

## GR-30 — A local checkout is a read cache and a worktree base, never a write target

Provision every lane workspace — CMR's own or any fleet spoke's — with
`ops/lane-workspace.sh` (ADR-0036): a fetched local mirror plus an isolated
`git worktree` off `origin/HEAD`, never a mutation of a shared checkout's own
index and never a base taken from whatever branch that checkout happens to be
parked on. Per GR-29, this ships with its own gate and self-test
(`make lane-workspace-self-test`), not as prose alone.

---

## GR-31 — No worktree, clone, or scratch tree on a tmpfs mount

Nothing transient lives on `tmpfs` (`/tmp`, `/var/tmp`, or any `tmpfs`). `tmpfs` is inode-capped
**and** RAM-backed, and it is a *shared* host resource: a tree placed there spends two scarce
budgets at once, on behalf of every other repository on the same host.

The failure mode is not "the machine got slow". At 100% inodes **no process on the host can create a
file in `/tmp`**, so unrelated repositories fail. Measured on `ElevatedIQ-AK` 2026-09-12: `/tmp`
reached `1,028,523 / 1,048,576` inodes (99%), of which five registered worktrees under `/tmp` held
**376,874 inodes (35% of the table) and ~4.8 GB of RAM** on a 30 GB host that was fully swapped; 17
worktrees across 5 repositories were still on `/tmp` later the same day. A shared-services pre-push
gate died with `OSError: [Errno 28] No space left on device` writing `htmlcov/`, caused by trees it
did not own.

Per GR-29 this is enforced, not advisory: `scripts/check-worktree-placement.sh`, wired as
`worktree-placement` in `mk/worktree.mk` and into `VERIFY_TARGETS`, fails when a **registered
worktree** resolves to a `tmpfs` mount, and `scripts/check-worktree-placement-self-test.sh` plants a
synthetic tmpfs worktree to prove the failure mode fires. A worktree belongs on the same persistent
filesystem as its repository — `findmnt -no FSTYPE --target <dir>` must not print `tmpfs` before any
worktree, clone, or scratch tree is created.

---

## GR-32 — A catch that does not record is a formality

Any error handler that suppresses a failure — `except …: pass`, a bare
`except`, `|| true` on a gated command, an `|| echo 0` fallback — must
**record** what it suppressed. The sanctioned sinks are a ledger row, the
command's structured output (the report/detail object a consumer reads), or a
re-raise; a suppressor that discards is indistinguishable from success, which
makes the check containing it a formality under GR-12.

The failure this closes is one level below GR-12's: the check *runs*, the
condition *does* fire, the exception *is* raised — and an `except` two lines
up eats it, so the check reports green. GR-12 covers the check that cannot
fail; GR-32 covers the check that fails and is not heard.

- **Evidence (the incident, not a hypothetical).** The machine-governance hook
  `bin/machine-awareness` in `~/laptop-manage` read a `runpy` **dict** with
  attribute syntax (`mod._cmdline(pid)`, and `mod.BUDGET[…]` twice more).
  `runpy.run_path()` returns a dict, so the attribute access raised
  `AttributeError`; it sat under `except Exception: pass`, the accumulator
  stayed empty, the ≥1500 MB escalation branch became unreachable, and the
  hook still reported `{"status": "ok", …, "tsserver_peak_mb": 4391}` — `ok`
  while a TypeScript server was at 4391 MB and crash-restarting on the legacy
  watcher. The one layer built to catch the blow-up was the one silently dead.
  Same family as the recorded `no-questions` meta-gate red at HEAD
  (`docs/ENFORCEMENT-GAP-ANALYSIS.md` Finding 0) and the `|| echo 0`
  double-append bug: in all four, the failure path and the success path are
  indistinguishable at the gate's output.
- **Per GR-29 this ships with its gate, not as prose.**
  `scripts/check-silent-catch.sh` (gates: `make silent-catch`): it scans
  tracked `*.py` **and** inline `python3 - <<'PY'` heredoc bodies inside
  tracked `*.sh` — an inline heredoc is source too, and that is exactly where
  the incident lived — failing on a handler whose only statement(s) are
  `pass`/`continue`. It exits 1 on a finding, 0 clean, 2 on usage.
- **Proven able to fail (GR-12).** The negative fixture
  `guardrails/gates/corpus/fixtures/silent-catch-planted/` is the verbatim
  incident (the `.py` shape plus the same bug inside an inline heredoc in
  `status_hook.sh`), wired into `guardrails/gates/corpus/corpus.json` as a
  `FAIL` row so `make gate-corpus` runs it, and
  `scripts/check-silent-catch-self-test.sh` additionally proves FAIL/clean/exit-2.
- **Documented exceptions are content-keyed and visible.**
  `guardrails/gates/silent-catch-allowlist.txt` (`pattern | path |
  content-substring | reason`, per `docs/SHELL-PATTERNS.md` §7 — never
  line-number keyed) records the pre-existing sites; an allowlisted finding is
  still printed, and an entry matching nothing is reported as stale on every
  run. Files under `guardrails/gates/corpus/fixtures/**` can never be
  allowlisted.
- **Scope note.** The bash shapes named above (`|| true`, `|| echo 0`) are
  already gated by `scripts/check-shell-patterns.sh` and
  `scripts/check-formality.sh`; this gate enforces the Python shape, which had
  no gate.

---

## GR-33 — An applier verifies its own post-state; revert is proven by round-trip

Everything declared (GR-5) is only half the guarantee. A declarative applier can
run, change nothing, and report success — the declaration looks applied, the gate
looks green, and the target is untouched — and a revert path that silently
under-restores is worse than none, because it consumes the rollback you discover
does not work during the incident.

Two mechanical conditions, for every CMR declarative applier (Terraform-adjacent
runners, settings appliers, onboarders, standards-sync, registry publishers):

1. **Post-state verification.** After applying, an applier reads its target back
   and compares it to the declaration; it exits non-zero on divergence and
   **names the divergent key**. "Applied" means "verified in effect", never "the
   write command returned 0".
2. **Apply → revert round-trip.** Every applier with a revert/uninstall path
   carries a test that applies to a **copy**, reverts, and asserts the copy is
   restored **byte-identically** (or a documented key-level equivalence where the
   format legitimately reorders). The pre-state is **captured, never inferred**,
   so revert restores whatever apply added, changed **and deleted**.

- **The trap this rule exists for.** Flat-vs-nested key shape: a VS Code-style
  target writes **literal dotted keys** (`"files.watcherExclude"` is ONE key),
  while other targets nest. An applier that splits a dotted key on `.` and looks
  up nested keys matches nothing, exits 0, and never applied the declaration — the
  removal that "silently did nothing".
- **Evidence (two silent no-ops, both found only by reading the state back).**
  (a) The `del_path`/`set_path` split-on-`.` bug above: a declared removal matched
  nothing and **exited 0**. (b) After fixing (a), the setter only updated
  *existing* leaves, so on revert a key that apply had **deleted** was never
  re-created — `revert` exited 0 and left the target half-applied. Both were found
  by round-tripping on a copy, which is exactly the test this rule makes
  mandatory and which did not exist before.
- **Per GR-29 this ships with its mechanism, not as prose.**
  `templates/applier/**` ships the three functions and the captured pre-state;
  `templates/applier/tests/round-trip.sh` is the gate, wired as
  `applier-round-trip` in `mk/infra-apply.mk` → `VERIFY_TARGETS` (the default
  `make verify` chain).
- **The harness has teeth (GR-12).** `templates/applier/tests/plant-defect.sh`
  plants the two incident defects into a copy: an under-restoring revert must make
  the round-trip test exit non-zero, and an applier that writes nothing must make
  `verify` exit non-zero naming the divergent key. A seam that has moved fails the
  plant **loudly** rather than going vacuously green. The template is declared, not
  hand-placed: `cmr new-applier` renders it and runs the round-trip in the render.
- **Scope note.** This is **not** a substitute for GR-5's `tf-drift`, which is a
  read-only `terraform plan` comparing declared config against live GitHub state:
  that validates no arbitrary applier's post-state and has no revert-round-trip
  requirement. (R2 · #847)

---

## Lessons loop (R10 applied to CMR)

Each rule above exists because its absence already caused — or would cause — a
recorded failure. The canonical write-up is `docs/RETROSPECTIVE.md` (CMR-807,
ecosystem retrospective), which tracks every lesson to the rule, ADR, or issue
that closes it. New lessons land there first and are promoted into this file only
once validated; a restated rule is not a new rule.

---

## Rule of thumb

Arrows **out** of the hub = governance, templates, guardrails. Arrows **in** =
catalog, telemetry, lessons. CMR never holds spoke application code (GR-1).
