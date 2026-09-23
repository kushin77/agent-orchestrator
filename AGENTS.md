# AGENTS.md — CMR (Centralized Module Repository)

## Connections

- **Owner-lane:** qa-sme
- **Class:** class
- **Connects-to:** consumes=none; called-by=none; gates=none
- **Env:** none
- **Updated-by:** qa-sme (2026-09-12)
- **Landed-by:** 2f022c3

Instructions for AI coding agents working in this repository. **Read this first** — it is
the canonical, single-source instruction file and is **model-agnostic**: behavior,
frontloading (goal-first), memory, and facts are stated here once, in a model-neutral
way. Per-runtime pointer files exist for each agent runtime and must not contradict this
file — the runtime → file map, the pointer contract, and the model-agnostic policy live in
`docs/MODEL-AGNOSTIC.md`. The full AI-guardrail build-out is tracked by
`board/epics/EPIC-05-ai-guardrails.md` (issues CMR-501..507); these files are the v0
**seeds** of that work.

## Goal of this repository (30 seconds)

CMR is the **HUB** — a centralized, private, IaC-driven **control plane** for the
`kushin77` spoke ecosystem. It governs repos via Terraform (branch protection, security,
teams), ships the module template + scaffolder, runs a private hybrid registry and
sync/vendoring engine, and distributes **AI guardrails** to every governed repo.
It hosts **standards and machinery — never spoke application code** (non-goal NG4).

Architecture is three planes (R13): **Control** (what CMR owns: governance, templates,
policy, guardrail definitions), **Data** (what spokes own: their app code and artifacts),
**Developer experience** (how humans and AI work: `cmr` CLI, instruction files,
issue/PR templates). See `board/ARCHITECTURE.md` → `docs/ARCHITECTURE.md`.

- Requirements & why: `board/INTENT.md` (R1–R15, operating principles, non-goals NG1–NG5).
- Milestones & epics: `board/MILESTONES.md`, `board/epics/EPIC-0*.md` — the epics are the
  source of truth that `board/materialize.sh` syncs to the GitHub issues board.
- Harvest ledger: `canibalization/INDEX.md` + `registry.csv`.

## Where the rules live (single source)

Do not restate long standards here — point at them, so agents stay current:

- **Golden rules** → `GOLDEN-RULES.md` (hub version lands under CMR-102; until then see
  `board/ARCHITECTURE.md` → "Cross-cutting contracts" and the list below).
- **Local guardrails hardening** → `guardrails/hooks/` (shell-aware + scale-aware
  PreToolUse hard-deny floor), `docs/SETTINGS-NG.md`, `docs/RCA-647.md` (compound-form
  evasions), `docs/PERMISSIONS-OPERATIONS.md` (per-runtime permission modes), and the
  per-runtime **parity contract** + its gate (indexed in `docs/README.md`).
  Code-native gate: `make guardrails-check` (+ `guardrails-check-self-test`).
- **Architecture** → `docs/ARCHITECTURE.md` (mirror of `board/ARCHITECTURE.md`).
- **Governance docs** → `docs/BRANCH_GOVERNANCE.md`, `docs/ADOPTION.md`,
  `docs/DISTRIBUTION.md` (CMR-102).
- **Decisions** → `docs/decision-records/` (ADRs — write one for a new decision).
- **Vendor boundary & handshake** → `docs/VENDOR-HANDSHAKE.md` (NG6).
- **Automation (code-native)** → `ops/README.md` (no GitHub Actions, GR-15).
- **Quality ladder** → `docs/SOLUTION-CLASSES.md`; **model tiers / FinOps** →
  `docs/MODEL-PROFILES.md`; **SME dispatch** → `docs/SME-PROFILES.md`;
  **parallel lanes** → `docs/EXECUTION-PLAN.md`. (Docs land under M1/EPIC-01; until
  present, follow `board/` + this file.)
- **Defaults & no-questions** → `docs/DEFAULTS.md` (decision defaults registry); GR-22
  + `guardrails/instructions/no-questions.md` (apply the default, escalate only per GR-22).
- **Agent operating doctrine** → `guardrails/instructions/agent-operating-doctrine.md`
  (orchestration-only lead, one issue = one lane = one branch, FinOps tier, "resolved
  with evidence", merge/push posture per GR-4, escalate before/after, never route
  around a denied action).

## How to work in this repo

1. **Read the issue.** Note its requirement tag (`R#`), its `fleet-ready` status, and
   its **`Verify:`** command. State the goal and success criteria before acting.
2. **Understand owns/lane.** Check `fleet/MANIFEST.tsv` if present — it declares lane
   ownership. Work **only in your lane's files**: `board/` (governance spec), `docs/`
   (canonical docs), `canibalization/` (harvest index), `guardrails/` (instruction layer),
   `infra/terraform/**` (governance-as-code, PR-gated). One issue = one lane; no two
   lanes share a file (see `docs/EXECUTION-PLAN.md`).
3. **Make the smallest change** that satisfies the acceptance criteria. No unrelated
   edits; match the existing style; no `TODO`/`FIXME`/debug leftovers.
4. **Run the issue's `Verify:` command** plus any applicable `make verify` targets
   (Makefile convention — targets appear under CMR-101/CMR-503).
5. **Report evidence**, never an unverified "done": exact command + output, files
   touched, and the R#/issue it closes.
6. **Declare AI-assistance + runtime** on PRs (CMR-505, for audit).
7. **Run the task retrospective gate** before declaring done —
   `guardrails/instructions/task-retrospective.md` (mandatory; "nothing to improve" must
   be stated explicitly, never skipped silently).

## Continuous loop + vendor notification

- **Continuous fanout loop:** standing behavior contract (subagent-first/max-parallel,
  ASAP/never idle, minimal operator questions, issue-first) is defined once in
  `guardrails/instructions/agent-behavior.md` — follow it (`fleet/DISPATCH.md`,
  `docs/EXECUTION-PLAN.md` are the dispatch mechanics).
- **GR-16:** every successful push to `main` notifies each `onboarded=true` vendor with a pinned
  `cmr:notify` issue summarizing the push (`channels/notify.sh`, `ops/run.sh notify-vendors`).
  Notification never merges or approves anything (NG2) and never touches vendor code (NG6).

## Board mandate (GR-20) + BAU

These are **standing business-as-usual** concerns — never re-request, never skip:

- **Everything goes on the board** — planned or ad-hoc, every task, lesson, enhancement,
  catch, and fix lands on the CMR issues board; off-board work is backfilled (GR-20,
  `docs/BOARD-MANDATE.md`).
- **Onboarding requests get official onboarding** — every role follows the one onboarding
  process (EPIC-13).
- **Vendor updates are rolling + single-pinned** — one pinned `cmr:notify` per vendor,
  rolling summaries (GR-16).
- **The model chooser is flash-by-default** — dispatch at the cheapest level that fits;
  flash/pro LOW-MED-HIGH-MAX ladder (EPIC-15, `docs/MODEL-PROFILES.md`).
- **Hygiene runs every loop** — `ops/run.sh hygiene` on the loop; a red gate blocks the
  next dispatch wave (GR-19).
- **Closed issues are unpinned** — a closed pinned issue is pollution (GR-20).

## Solo-dev posture (permitted — not a DON'T)

- **Autonomous merge/close for the active owner** is **allowed** while
  `ops/scale-tripwire.sh` has not fired, after the issue's `Verify:` command and
  `make verify` pass; no human approval gate is required for the active owner's own
  work. Treat merge as execution, not as a bypass of verification. Green CI remains
  mandatory — that gate is never lifted. Canonical rule: `GOLDEN-RULES.md` GR-4
  (ADR-0027/0030, amended by ADR-0033). Once the tripwire fires, this reverts to
  requiring an independent reviewer.

## Hard DON'Ts

- **No secrets** in code, files, or git history — env / GSM / secret managers only;
  gitleaks runs in verify. Never commit a real token or key.
- **No ad-hoc `terraform apply`.** Infra (`infra/terraform/**`) changes are
  PR → `terraform plan` → human/CI-reviewed `apply`. New infra ships **flag-gated OFF**
  by default. No console clicks (IaC mandate).
- **Hub never hosts spoke app code** (NG4). Do not vendor/submodule/subtree a live app
  into CMR — it hosts the manifest, governance, and registry indexes only.
- **Never do a vendor/spoke's work (the boundary, NG6).** CMR develops only CMR. Vendor,
  module, and spoke work happens in their own repo, driven by their own issue board; CMR only
  files **direction issues** there. Vendor requests to CMR default to the CMR board
  (`docs/VENDOR-HANDSHAKE.md`). Solo-dev carve-out (GR-14,
  `docs/decision-records/ADR-0030-solo-dev-operating-posture.md`): while the scale
  tripwire has not fired, direct writes/self-merge to an onboarded, kushin77-owned spoke
  repo are permitted with a green spoke gate and a same-work direction issue recording
  it — repo-governance/branch-protection settings changes remain excluded at any scale.
- **Provenance is mandatory for canibalized assets.** Any harvested code recorded in
  `canibalization/INDEX.md` / `registry.csv` carries its source (repo, path, license).
- **Respect parallel-lane ownership.** Don't edit files owned by another agent/lane
  (`docs/`, `fleet/`, live `board/` files, `guardrails/prompts/` until EPIC-05).
- **No mass force-push / history rewrite.** `main` is protected (CMR-104); every change
  lands via a reviewed PR with green CI. Who may merge a self-authored PR is
  tripwire-conditional — `GOLDEN-RULES.md` GR-4 governs (see the solo-dev posture above).

## Verification

```bash
make verify                       # gate of record (CMR-101/503): shell syntax + markdown
                                  #   lint + YAML validation → + format/secrets/conformance
make guardrails-check           # guardrails escape-case harness + assertion gate (#476/#647)
bash board/materialize.sh ...     # board materialization; always --dry-run first
bash -n <script>.sh               # shell syntax check
python3 -m json.tool <file>.json  # JSON validity
```

Before every `git commit`, run the pre-commit self-check —
`guardrails/instructions/pre-commit-self-check.md` (branch is not `main`; staged files
match the current lane).

The code-native runner applies the same gate on every PR (`ops/run.sh verify` — GitHub
Actions is **disabled**, GR-15). Exit codes must be **real** — a gate that cannot
fail is a formality and will be rejected (testing-suite lesson).

## Lessons (hub learns, R10)

Every task ends with the retrospective gate —
`guardrails/instructions/task-retrospective.md` — not just the tasks that happen to
surface a lesson.

The ecosystem retro (`docs/RETROSPECTIVE.md`, CMR-807) records what Waves 1–7 proved
and where they broke; every lesson there folds into a rule, ADR, or issue. Two traps
repeat often enough to keep here:

- **`gh issue view` is unreliable on this repo** — the classic-Projects GraphQL
  (`projectCards`) is deprecated and returns stale or wrong issue bodies. Use
  `gh api repos/kushin77/CMR/issues/N` (REST) for authoritative bodies and titles.
- **`make verify` shell-syntax scans only `SHELL_DIRS`** (`board channels controller
  fleet scripts ops`). Scripts under `registry/**`, `sync/**`, and `guardrails/**`
  are not scanned — run `bash -n <script>` directly. `bash -n` reports a syntax
  error with exit **2**, not 1.

## Cross-cutting contracts (short form)

| Contract | Rule | Where |
|---|---|---|
| Versioning | SemVer `vX.Y.Z` git tag = truth | EPIC-03 |
| Consumption | Pinned; upgrades via PR only | EPIC-03/04 |
| New module | Born from `cmr new` (standards by birth) | EPIC-02 |
| Guardrails | Instruction files REQUIRED in every governed repo | EPIC-05 |
| Governance | Declared in Terraform; applied by the code-native runner on merge | EPIC-01 |
| Blast radius | Module changes run dependents' integration tests | EPIC-04/08 |
| Provenance | Every harvested asset records source | EPIC-06 |

Full table: `docs/ARCHITECTURE.md` → "Cross-cutting contracts".
