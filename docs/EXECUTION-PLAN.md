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

1. Work in your **own git worktree** under `$TMPDIR` (never the shared main
   checkout).
2. Read the issue spec (`gh api` is authoritative — `gh issue view` trips the
   classic-Projects GraphQL bug on this org); implement the acceptance criteria
   to completion.
3. Stay in your lane; smallest focused diff; no unfinished markers or debug
   leftovers.
4. Commit with `Refs kushin77/agent-orchestrator#<n>`; push; open a PR whose
   body includes `Closes #<n>` and an AI-assistance declaration.
5. **Verify before done (GR-12):** run the issue's `Verify:` command and
   `make verify`; paste the **actual output** as evidence on the PR.
6. **Merge after green** (owner autonomous-merge mandate) — never merge failing
   work.
7. Close the issue with the evidence comment; re-check the board before
   standing down (never idle).

## 5. FinOps / model discipline

- Default dispatch at the **cheapest capable tier** (flash/LOW).
- Escalate (pro/HIGH) only on observed difficulty or for security/authN lanes —
  never pre-emptively.
- No two agents touch the same file in the same wave; conflicts are a cost,
  not just a correctness problem.

## 6. Gate of record

`make verify` is the repo's gate of record until CI lands (issue #6). It is an
**honest composite gate** (shell syntax, YAML, JSON, docs, secrets) — every
check can genuinely fail (no-false-green doctrine). A red gate blocks the next
dispatch wave.
