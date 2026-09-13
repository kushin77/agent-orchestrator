# Execution Plan — agent-orchestrator

> Parallel dispatch contract: **one issue = one lane = one branch**, no two
> lanes share a file in the same wave. Agents start at
> [`../AGENTS.md`](../AGENTS.md); lane ownership lives here and is kept in sync
> with the GitHub issues board (the canonical roadmap, EPIC-00 = issue #4).

## 1. Phase / work-item map (issue → pillar lane)

Work items are numbered 01–45 across phases 0–8 (labels `phase:N`, `pillar:X`,
`priority`).

| Phase | Scope | Issues (work items) | Owning lane / dir |
|-------|-------|---------------------|-------------------|
| 0 | Foundations | #5 (01), #6 (02), #7 (03), #8 (04), #49 (45 spike), #48 (44 spike) | foundation — root docs, `scripts/`, `Makefile`, `.github/` (issue #6) |
| 1 | Agent Registry & Profiling | #9 (05) … #14 (10) | registry — `registry/` |
| 2 | Model Gateways | #15 (11) … #20 (16) | gateway — `gateway/` |
| 3 | State-machine execution | #21 (17) … #25 (21) | engine — `engine/` |
| 4 | Security & guardrails | #26 (22) … #30 (26) | guardrails — `guardrails/` |
| 5 | Observability | #31 (27) … #34 (30) | telemetry — `telemetry/` |
| 6 | Tenant identity/RBAC | #35 (31) … #38 (34) | identity — `identity/` |
| 7 | Control plane / portal | #39 (35) … #42 (38) | control-plane — `control-plane/` + `portal/` |
| 8 | Autonomous ops / governance | #43 (39) … #47 (43) | autonomous-ops — docs/guardrails/infra |
| — | E2E gate | #46 (42, P0) | top-level negative-control gate |

EPIC-00 (issue #4) is the parent index; it closes **last**, only after all
children are closed and the product is live flag-gated.

## 2. Wave sequencing (dependencies)

- **W1:** #5 (foundation skeleton — this issue) and #8 (cannibalization index)
  in parallel. Disjoint files: #5 owns root docs + hygiene + pillar placeholders;
  #8 owns `docs/CANNIBALIZATION.md` + index assets.
- **W2:** #6 (CI/CD + IaC, needs #5's Makefile/layout) and #7 (golden rules +
  ADRs, needs `docs/` layout) in parallel.
- **W3:** #49 and #48 spikes (research; feed later phases' recovery).
- **Then per phase:** phase-N lanes run in parallel after phase N−1 foundation
  lands; each lane owns distinct files under its pillar dir.
- **#46 (E2E negative control, P0)** is a top-level gate that must pass at the
  end.

## 3. Lane ownership (no two lanes share a file)

| Lane | Owns | Model tier | Gate |
|------|------|------------|------|
| foundation | root docs, `AGENTS.md`/mirrors, hygiene files, `Makefile`, `scripts/`, `docs/` layout | flash/LOW | `make verify` |
| infra | `infra/`, CI/CD (issue #6) | flash/LOW → pro/HIGH for apply | `make verify` + terraform |
| registry | `registry/**` | flash/LOW | `make verify` |
| gateway | `gateway/**` | flash/LOW | `make verify` |
| engine | `engine/**` | flash/LOW | `make verify` |
| guardrails | `guardrails/**` | pro/HIGH (security) | `make verify` |
| telemetry | `telemetry/**` | flash/LOW | `make verify` |
| identity | `identity/**` | pro/HIGH (authN/z) | `make verify` |
| control-plane / portal | `control-plane/**`, `portal/**` | flash/LOW | `make verify` |
| autonomous-ops | phase-8 surface | flash/LOW | `make verify` |

**Contract freeze:** when two lanes need a shared field/helper, whichever lane
lands the schema/types change first owns the contract; the others consume the
field name, never the files.

## 4. Dispatch contract (per soldier/agent)

1. **Claim the issue.** `python3 governance/dispatch/cli.py claim --issue <n>
   --agent <id> --lane <lane>` — refused unless the issue is the next step in the
   active chain (frontier of the active milestone, a child of an issue you hold,
   or the successor of one you advanced). A second claim on an in-flight issue is
   refused too. Release the claim when the chain advances. A brain directive
   recorded in `.fleet/sent` authorizes off-frontier work:
   `claim --directive <id>`; without one, off-frontier claims stay refused.
2. **Open your lane — do not work in the shared checkout.**
   `python3 governance/isolation/cli.py open --issue <n> --agent <id> --lane
   <lane>` mints your session identity, creates the lane's **own git worktree**
   on branch `issue-<n>` cut from `origin/master`, and stamps your session's
   signature into that worktree's own config with `git config --worktree`, so two
   lanes on one machine never sign as each other. Load the identity into your
   shell with `eval "$(python3 governance/isolation/cli.py env --issue <n>
   --agent <id>)"` — it exports `AO_SESSION_ID`, `AO_ISSUE`, `AO_BRANCH`,
   `AO_WORKTREE` and `GIT_AUTHOR_*`/`GIT_COMMITTER_*`. Isolation is a state the
   machine establishes, not a convention an agent is asked to honour.
3. Read the issue spec (`gh api` is authoritative — `gh issue view` trips the
   classic-Projects GraphQL bug on this org); implement the acceptance criteria
   to completion.
4. Stay in your lane; smallest focused diff; no unfinished markers or debug
   leftovers.
5. Commit with `Refs kushin77/agent-orchestrator#<n>` (every commit you author —
   the lane audit checks each one, not just the branch tip); push; open a PR whose
   body includes `Closes #<n>` and an AI-assistance declaration.
6. **Verify before done (GR-12):** run the issue's `Verify:` command and
   `make verify`; paste the **actual output** as evidence on the PR.
7. **Merge after green** (owner autonomous-merge mandate) — never merge failing
   work.
8. **Close the item out — every artifact terminal.** Merging is not the end of
   the item. Run `python3 governance/lifecycle/cli.py close --issue <n>`: it
   drives the remaining **close-out** steps in dependency order (consume the
   authorisation directive *before* releasing the claim, delete the source
   branch, close the issue with evidence, reclaim the lane) and re-derives the
   closure invariants afterwards. It reports what is still broken rather than
   success it cannot evidence; every artifact must reach its **terminal state**.
   `python3 governance/lifecycle/cli.py audit` reports any item that did not.
9. Re-check the board before standing down (never idle).

## 5. Chronological dispatch rule (mandatory)

Agents must not schedule work by board visibility or board scavenging. The issue
queue is executed in chronological, dependency-aware order.

1. **Claim only the next required issue.** The active issue is the next item in
   the current milestone / phase chain or the child needed to close the active
   parent.
2. **No unrelated board picking.** A GitHub issue that is not required to close
   the current issue or advance the active dependency chain is not eligible.
3. **Parent/child chain wins over board rank.** A parent issue and its
   dependent child work together as one sequence; the board only exposes the
   next valid item in that chain.
4. **Kanban drift is forbidden.** If a task is not directly tied to the current
   open issue, it is deferred until the chain reaches it.
5. **Close the chain before launching new work.** New issue selection is only
   allowed after the current chain reaches verification/closeout or when the new
   issue is the direct required continuation of the same chain.

This is a governance rule, not an optimization preference. Any agent that
starts choosing issues ad hoc is violating the repo's execution contract.

## 6. FinOps / model discipline

- Default dispatch at the **cheapest capable tier** (flash/LOW).
- Escalate (pro/HIGH) only on observed difficulty or for security/authN lanes —
  never pre-emptively.
- No two agents touch the same file in the same wave; conflicts are a cost,
  not just a correctness problem.

## 7. Gate of record

`make verify` is the repo's gate of record until CI lands (issue #6). It is an
**honest composite gate** (shell syntax, YAML, JSON, docs, secrets) — every
check can genuinely fail (no-false-green doctrine). A red gate blocks the next
dispatch wave.
