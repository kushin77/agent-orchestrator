# Copilot instructions — agent-orchestrator (GitHub Copilot pointer)

This file is the **GitHub Copilot** runtime pointer for
`kushin77/agent-orchestrator`. It contains only Copilot-specific invocation
context. **Read `AGENTS.md` first** — it is the canonical, model-agnostic
instruction file for this repo; this file never contradicts it.

## What this repo is

`agent-orchestrator` is the **AI-agent-orchestration service control plane**
(EPIC-00, issue #4): a multi-tenant SaaS control plane that organizes, governs,
and manages commercial AI agents (Claude, DeepSeek, Copilot, Gemini, local
Ollama). Five-pillar architecture — Agent Registry & Profiling (`registry/`),
Model Gateways (`gateway/`), State-machine execution (`engine/`), Security &
guardrails (`guardrails/`), Observability (`telemetry/`) — plus cross-cutting
tenant identity/RBAC (`identity/`), control-plane/portal (`control-plane/`,
`portal/`), and autonomous ops/governance (`governance/`, `infra/`). Default
branch is `master`. See `docs/ARCHITECTURE.md` for the source of truth.

## Commands

```bash
make verify   # gate of record: shell syntax + YAML + JSON + docs + secrets
make help     # list all targets
bash -n <file>.sh
```

## Copilot-specific notes

- **Precedence:** `AGENTS.md` is canonical. `CLAUDE.md`, `.cursorrules`, and
  this file defer to it and never contradict it. `docs/GOLDEN-RULES.md`
  (AO-GR-1..28) is the canonical product spine; root `GOLDEN-RULES.md` records
  the CMR vendor-module ratification.
- **Workflow:** read the issue (its `Verify:` command and lane ownership); touch
  only your lane's files; smallest focused diff; run `make verify` and report
  actual output as evidence before claiming done; declare AI-assistance +
  runtime on the PR.
- **Local-code-first (GR-17):** debug against this checkout first; query the
  **CMR indexer KB** for cross-repo/org-wide `kushin77` knowledge.

## Hard DON'Ts

- No direct pushes to `master`; changes land via a PR against `master` (squash
  merge). No merging failing work — green `make verify` evidence required first.
- No secrets in code, files, or git history (env / secret managers only).
- No ad-hoc `terraform apply` / console clicks; new infra ships flag-gated ON by default (AO-GR-6).
- No editing `vendor/` (pinned CMR submodule); never commit `.research/`
  clones; no unfinished markers or debug leftovers.

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
