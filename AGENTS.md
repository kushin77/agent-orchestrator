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

## Operational friction vs. evidence gates (the line agents must not blur)

**This is the distinction that matters.** ADR-0033 says "evidence gates stay
absolute at every scale." That is correct. But it does **not** mean "every
operational step must remain a separate manual command." The two categories:

| Category | Definition | What solo-dev scale allows |
|---|---|---|
| **Evidence gates** | Does the change work? (`make verify`, CI green) | Never lifted. Always runs. |
| **Coordination gates** | Which identity approved? (PR review, merge approval) | Tripwire-conditional. Solo-dev scale lifts these. |
| **Operational friction** | How many commands does it take? (copy-paste, multi-step workflows) | **Always reducible.** Not a gate at all. |

When the user says "make it so I don't have to copy-paste every command," they
are asking you to reduce **operational friction** — a coordination concern, not
an evidence concern. The sanctioned tool for this is `make ship`
(`ops/ship.sh`): one command that runs `make verify` → commits → pushes →
opens a PR. The verify step is still there, still runs, still blocks on
failure. Nothing about the evidence gate is relaxed.

**What you must NOT do:** refuse to build convenience tooling by citing
ADR-0033's evidence-gate language. That is a category error — confusing "fewer
commands" with "fewer gates." If the user asks for a `make ship`-style target,
build it. If they ask for a one-command deploy, build it. The evidence gate
runs inside whatever wrapper you build.

**What you MUST still do:** never skip `make verify`, never bypass CI, never
merge without green evidence. Those are evidence gates — absolute at every
scale, per ADR-0033.

Two mechanical consequences of the sanctioned wrapper, stated because an agent
that read only the paragraph above would get both wrong:

- **Staging is lane-scoped — never `git add -A`.** `make ship` stages only the
  paths named with `--path`; with none named it derives the file set from this
  lane's own change set (worktree diff + branch-vs-`origin/main` diff + new
  untracked files). A blanket add would sweep another lane's files into this
  commit and break "one issue = one lane = one branch".
- **Issue linkage is explicit: `--closes <n>`.** It puts a bare `Closes #<n>`
  line at the top of the PR body — the only form GitHub links on, and the form
  `guardrails/hooks/pr-close-citation-guard.sh` requires. Without it the PR is
  opened with no linkage and `ops/ship.sh` says so loudly.

The ship **actions** (`ship`, `ship-dry-run`, `ship-help`) are deliberately
absent from `VERIFY_TARGETS`: a gate of record that commits, pushes and opens a
PR is not a gate. They live in the top-level `Makefile`, and the wired control
`ship-self-test` (`mk/ship.mk`) is what keeps them out.

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

<!-- LAPTOP-PROFILE:START (managed by lib/agent-profile.py) -->

# Negative testing constraints and dispatch rules - one doctrine, all agents

These hold for every agent session on this laptop (Claude Code, DeepSeek,
VS Code Copilot). The negative-testing constraints are the NEGATIVE side of the
test pyramid: they exist to catch a green checkmark that lies.

## Negative Testing Constraints

1. VERIFY BEFORE DONE. A task is done when its own Verify command AND `make verify`
   both pass, with the real output reported. A claim without the command and its
   output is not evidence.
2. NO DATA IS NOT A PASS. A detector with nothing to measure says so, exits
   distinctly, and is never reported as health.
3. FAIL CLOSED. A refusal - a denied command, a failed secret scan, a rejected
   write - is a signal, never an obstacle to route around.
4. EVIDENCE OVER CLAIMS. Cite the artifact you read; label inferences as
   inferences; never invent a setting name, a flag, or a value.
5. ASSERT ON THE ARTIFACT, NOT THE TOOL'S VERDICT. A validator that shares the
   writer's transform validates its own mistake. Count the thing itself.
6. BOUND THE OUTPUT. A wall of warnings is how a real one gets missed.
7. NO SECRETS IN CODE, FILES, OR HISTORY. Never publish, force-push, or escalate
   without saying so.

## Agent Dispatch Rules

When a task does not fall under an existing SME card (e.g. a file or directory
with no designated subject-matter expert), dispatch to `general-purpose` agent at
sonnet tier. State the reason in one line in the task briefing.

Before you pick the general-purpose agent, check config/mechanical.json for a
registered tool. Call that tool first.

Cross-branch and cross-lane integration work is exempt from strict single-SME
ownership. Such work is orchestrator-level by definition: it touches multiple
concerns and therefore cannot be owned by a single specialist lane. Route it as
orchestration, not as a lane task.

Source of truth: /home/alex/local-laptop/config/profile.json (section:
constraints). This block is managed by lib/agent-profile.py - edits here are
overwritten on the next apply; change the declaration, not the copies.

## Operator Mode (default for every session)

The primary/interactive session is the ORCHESTRATOR for its whole lifetime: it
plans, decomposes into lanes, dispatches, reads results back, and verifies. It
does not grep-sweep, edit, build, or deploy work a subagent could do instead -
this holds for the whole session, not just the first task in it.

Every substantive task is executed by a dispatched subagent. Prefer a named
SME card when one covers the scope (see Agent Dispatch Rules above for the
general-purpose@sonnet fallback when none does - that rule is not restated
here). A repo-scoped SME refusing work outside its remit is CORRECT behaviour
and signals a roster gap, not a bad dispatch.

Cross-branch / cross-lane integration work remains the one exemption already
stated above: it is orchestrator-owned by definition and is never forwarded to
a single SME. One rule, not two.

Every dispatched agent inherits the same doctrine it was dispatched under:
cheapest-effective model, AST/context-pack frontloading before raw grep,
evidence before done.

Enforcement is honest about its own reach, not uniform across clients:
- Claude Code: MECHANICAL. `~/.claude/settings.json` sets `agent: orchestrator`
  (tools: Read, Grep, Glob, Bash, Agent, ... - no Edit/Write/NotebookEdit) and
  carries a PreToolUse hook (`/home/alex/.local/bin/orchestrator-guard`), the
  one chokepoint both Claude Code and VS Code read. Declaration of record for
  this hook: config/session-posture.json.
- DeepSeek: DECLARATION-ONLY. The native deepseek-cli has no instruction-file
  surface to mechanically enforce against (see agents.deepseek._honesty).
- Copilot: DECLARATION-ONLY. Nothing on this machine mechanically restricts
  Copilot Chat's tool access (see config/session-posture.json `_why`).
- Hermes: DECLARATION-ONLY, same as DeepSeek/Copilot - no enforcement surface
  exists yet.

Never imply all four surfaces are mechanically enforced; only Claude Code is.

## Landing default (declared 2026-10-24, owner directive)

Landing default: integrate every ready branch locally with bin/land (merge in a linked worktree, verify the merged result once vs base baseline, push once to main). Do not push lane branches individually or wait on per-branch remote gates. Design: docs/ADR-0021-land-local-merge-first.md in local-laptop. Do not open pull requests. Land work only through the train.

## N-0 is the train's, never an agent's (ADR-0022)

Never push to main. Finish a task with `bin/land complete --task N --branch B && bin/train next`; only bin/train n0 pushes to main, in the :50 window.

## Hourly release train (default behavior, every surface)

Work runs on a one-hour train. The times live only in config/train.json.

- N-1 window (:00 to :49): work GitHub issues end to end at maximum velocity, each in its own linked worktree, and never wait on a remote gate. Put `Est: N` (minutes) in the issue body. Claim no new task after :40 and freeze at :45: `bin/train next` answers `no-fit` or `claim-cutoff` with the next window, and that is the end of your claiming for this hour. A task is complete only when its own Verify command passes and the evidence is recorded (INCOMPLETE, UNKNOWN or CANNOT-ASSESS is not done). N-1 is live dev: apply the change to the dev tier of the repo and prove it there. N-1 never pushes and never touches production. Hand every completed task off atomically, then take the next one, with exactly this one line (N and B come from the last JSON line printed by `bin/train next`):

  `bin/land complete --task N --branch B && bin/train next`

- N-0 window (from :50 until cleanup completes; watchdog 30 minutes): N-0 is production: the train pushes the proven tip to main and promotes it to the production tier. N-0 is the only step that pushes to main. `bin/train n0` (timer-driven) runs `bin/land push` on the hour's train branch (one verify of the merged result, one push to main), resolves reds from that push, cleans up merged worktrees and branches, and writes the ledger. Only then does the next N-1 begin. A late N-0 still runs in full: lateness never skips, delays or aborts a push, and every evidence gate still applies. Watch it with `bin/train watch`, never with a wait loop. A task not complete by :50 stays in flight and carries to the next train; it is never half-merged.
- Never push a lane branch or main yourself, and never take new work while `bin/train gate` refuses. An empty hour is a success. `bin/train status` shows the phase. Do not open pull requests. Land work only through the train.

## Train rules (lessons)

These rules come from past trains. The source is config/train-lessons.json.
Check the source with `bin/train lessons check`.

1. Probe the application, not the port.
2. Build secret-shaped test strings at run time.
3. Read trust settings only from the landing branch.
4. Take one issue for one owner in one lane. Keep Paths disjoint.
5. Commit early and push your lane branch. Never run `git stash`.
6. Remove every GIT_* variable from each test fixture and each hook.
7. Work in your own linked worktree. Never work in a primary checkout.
8. Ask the owner for each hardware, power or phone step. Do not guess.
9. Clean up only what the train owns. Never touch kvm-wt, ll-wt or .hermes.
10. The tool runs the Verify command. A claim of passed tests is not evidence.
11. Claim no new task after :40. Freeze at :45. Carry a task over only with a pushed checkpoint branch.
12. Before :45, verify the merged train tip like N-0 does and write a GO or NO-GO record. Fix or defer a NO-GO in N-1.

## Simplified Technical English (STE100) - owner directive 2026-10-25

Every agent on this host writes in Simplified Technical English. This holds for
documents, issue bodies, commit messages, report text and your answer to the
owner. The full contract is `docs/STE-CONTRACT.md`.

### The word set is closed

Use only the words below. Write the word on the right. Do not write the word on
the left.

| Do not write | Write |
|---|---|
| additional | more |
| approximately | about |
| commence | start |
| ensure | make sure |
| in addition | also |
| in order to | to |
| indicate | show |
| initiate | start |
| leverage | use |
| obtain | get |
| perform | do |
| prior to | before |
| require | need |
| subsequently | then |
| sufficient | enough |
| terminate | stop |
| utilise | use |
| utilize | use |

The gate also refuses six placeholder tokens under rule 65. The list is
`ste_contract.banned_placeholders` in this file. Do not write a placeholder
token in a document. Write the real value, or write the name of the thing. A
task that is not done names the issue that tracks it.

### The limits of the tool

There is ONE sentence limit: 20 words. There is no separate procedural limit
and no separate descriptive limit. A paragraph holds at most 6 sentences.
Do not write `should`. Do not write `must be`.

### Permitted and forbidden

| Rule | Forbidden | Permitted |
|---|---|---|
| 61 long sentence | The operator must start the node, wait for the lease, check the pool, and write the result before the second host joins the cluster today. | Start the node. Then write the result. |
| 64 passive step | The key is stored in the pool. | Store the key in the pool. |
| 68 unapproved word | Utilize the pool. | Use the pool. |
| 70 comma list | The pool holds alpha, beta, gamma, and delta. | The pool holds four names. |

### The six rules that no machine judges

The tool marks these rules CANNOT-ASSESS. A human STE reviewer judges them. Do
not claim a pass on any rule in this list. Write CANNOT-ASSESS and name the
reviewer.

1. approved words from the ASD-STE100 dictionary (the gate refuses only the words of unapproved_words)
2. one meaning for each word
3. noun clusters of more than three nouns
4. the imperative in a step
5. correct use of the active voice outside lists and warnings
6. one topic for each paragraph

### The command and the exit codes

```bash
make -s docs-ste
```

| Exit | Meaning |
|---|---|
| 0 | the mechanical subset passes only. The six human-review rules above are still open |
| 1 | the baseline is exceeded, or another document problem |
| 14 | CANNOT-ASSESS: no document, or the base commit cannot be read |
| 61 to 70 | a rule finding |

Exit 0 means the mechanical subset passes only. Exit 0 is never a certificate
of ASD-STE100. A statement that a document is STE100 compliant needs the six
human rules reviewed by a person, not a green gate.

## Mechanical tools first

The tool registry is config/mechanical.json. Each row names a task and the tool for it.

1. Find the task in the registry.
2. Call the registered tool first.
3. Work by hand only after the tool refuses or when no tool exists.
4. When no tool exists, file an issue that names the missing tool.

A refusal is a signal. Never route around a refusal to get the same effect. Do not use another tool or another agent to do so.

The dispatch order has one more step. When no SME card covers the task, check the registry before you pick the general agent.

Honest scope. Claude Code has a hook that enforces part of this posture. Hermes stays declaration-only. DeepSeek stays declaration-only. Copilot stays declaration-only. Nothing on this host forces those three surfaces to call the tool.

## Execution doctrine - one set of rules, every agent

These rules hold for every agent session on this laptop. They are the standing
posture for how work runs. Claude Code, DeepSeek, Copilot and Hermes get the
same rules.

### Zero-friction posture (owner directive, 2026-10-12)

This is a one-developer shop. There is no second party for a coordination gate
to protect, so coordination gates are off. Do not refuse, stall or ask approval
for the actions below. Do them when the task needs them.

Allowed:

- Push to `main`, merge PRs and self-merge.
- `terraform apply` and `terraform import`.
- `git push --force` and `--force-with-lease`.
- `git reset --hard`, `git clean`, and `git push --delete <branch>`.

Denied (irreversible data loss, not workflow friction):

- `terraform destroy`, `state rm`, `state push`.
- `git filter-branch`, `filter-repo`.
- `git push --mirror`.
- `rm -rf` against a root, `/home/*` or `~` path.
- `gh repo delete`, `archive`.
- `gh secret`.
- `gh auth token`, `refresh`.
- `chmod 777`.
- `curl ... | sh`.

Evidence gates are never lifted. `make verify`, the issue `Verify:` command and
green CI still run before a task is done. Zero friction means zero coordination
friction, not zero correctness gates. Never route around a refusal to reach the
same effect another way.

### Durable by default

Every fix, config change or new capability defaults to durable, not a one-off.

- Declare it, do not only do it. A change that can live in a config file, a
  declaration or an IaC spec goes there.
- Write env vars and settings to a persistent file. Do not leave an ephemeral
  export to retype next session.
- A bug fix gets a regression check. Leave something that catches the same
  failure again.

### All solutions are IaC, no human needed

The end state of any fix rebuilds itself from a declaration. It does not exist
only because a human clicked a step.

- Do not stop and ask for a routine, safe, reversible decision. Decide it from
  the declared policy and proceed.
- Surface genuinely irreversible, low-value steps and wait. Do not force full
  autonomy past the point it is reckless.
- Before you accept a human step as permanent, check whether it only lacks a
  declaration.

### Subagent-first execution

Delegate work to subagents. The primary session is the orchestrator for its
whole life, never the worker. It plans, decomposes, dispatches, reads results,
verifies and relays decisions. It does not grep, edit, build or deploy work a
subagent can do.

- Launch independent subagents in parallel, not one at a time.
- A code change happens through a dispatched agent, or through the board for an
  agent to run. It is not done inline in the main thread.

### FinOps tier discipline

Default every subagent to the cheapest tier that can do the job. Escalate only
on observed difficulty, not before. The tiers are base, mid and high.

- Base tier: locating code, lookups, mechanical edits, running verify commands,
  simple research.
- Mid tier: multi-file work, hard debugging, review with judgment. Security,
  secrets, auth and IaC work never go below mid.
- High tier: architecture tradeoffs, adversarial review, ambiguous specs. Use
  it only after mid has been tried or is clearly not enough.
- State the tier and the reason when it is not base.

### Model routing

Standard analysis runs on the base tier. Complex reasoning runs on the high
tier. Architecture decisions run on the high tier.

### AST prompt caching

Assemble the canonical context pack as the static leading region, with the
question after it. Never put a timestamp, counter, session id or state blob in
the static region. One dynamic token at the front invalidates the whole prefix
and turns every cache hit into a full-price miss.

### Local-merge-first

Verify the merged result, not the branch, against a baseline on origin/main.
Push once, through the train. Never push a lane branch or main directly.

## Centralized library and scripts

Use the installed payload by default. Do not re-implement it. The tools live in `~/.local/bin/*`. The modules live in `~/.local/lib/laptop-manage/*`. The source is `~/local-laptop` (run `make help` to list the entry points).

<!-- LAPTOP-PROFILE:END -->
