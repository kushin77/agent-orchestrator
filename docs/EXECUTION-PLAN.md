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

**Single-writer shared build files (RC-8, #559).** `Makefile`,
`scripts/verify.sh`, `scripts/pytest-suites.txt`, and `docs/README.md` are the
sole-writer territory of the gate-wiring lane (RC-8, issue #559). Per issue
#603, `scripts/gate-coverage-baseline.txt` joins that list: it is the
gate-coverage exception list, its live `script` rows are deferrals to the
wiring lane that will retire them, and it is the file the detector refuses to
let any other lane touch productively (a planted row fails the provenance
checks). A lane that delivers an unwired artifact must defer to RC-8, never
edit the baseline itself.

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
6. **The active epic is a chain edge.** A `Parent:` edge to the epic the fleet is
   currently driving (`.board/focus.json`, epic focus) is a dependency edge: its
   children are claimable under the additive reason `active-epic-child`. This
   tightens the milestone frontier rather than opening the board — an item with
   no such edge is still refused `no-chain-edge`.
7. **Out-of-epic work is parked, not dispatched.** While a focus is active, work
   outside the active epic is refused `out-of-epic-pooled` — even as the
   milestone frontier, because a frontier that interleaves epics is exactly the
   incoherence the focus removes. The deferral is recorded in
   `.board/pool.jsonl`, so it is parked rather than dropped; a `Blocked-by:`
   blocker of an active-epic child is promoted just-in-time through the existing
   `claim --directive` path, and the pool drains (reporting what it drained) once
   no epic is workable.

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

## 8. Fan-out capacity (epic #707, lane F3 / issue #718)

The fleet fans out to the **maximum** number of agents by default — but
"maximum" is resolved, not chosen. Every dispatch cycle the ceiling is:

```
effective = min(pool_size, disjoint_ready_lanes, resource_ceiling)
```

`governance/dispatch/cli.py focus` reports the two bounds that do not depend on
the ready set; `fleet/terminal.py` resolves the third and the minimum per cycle:

```
capacity: max_agents_default=10 (FLEET_MAX_AGENTS/focus/FLEET_SISTER_POOL) \
resource_ceiling=12 (12 lane(s) — bound by RAM: RAM 19068 MiB / 1536 MiB = 12, \
/tmp 12578 MiB / 512 MiB = 24) — effective = min(pool, disjoint-ready-lanes, \
resource_ceiling); the disjoint bound is resolved per dispatch cycle over the ready set
```

### 8.1 The three bounds, and why each exists

| Bound | Resolved from | The failure it prevents |
|---|---|---|
| `pool` | `FLEET_MAX_AGENTS` → the focus's `max_agents` (a positive value) → `FLEET_SISTER_POOL` / `DEFAULT_POOL_SIZE` (`0` in either of the first two means "the pool") | an operator cannot cap the fleet without a code change |
| `disjoint` | the file sets the ready lanes declare (`task.files`), pairwise-intersecting lanes refused (AO-GR-24) | **27 source-file collisions across 14 lanes** — raising the agent count multiplied conflicts instead of throughput |
| `resource` | `MemAvailable` / per-lane RAM budget, and free space on `/tmp` / per-lane `/tmp` budget | **49 concurrent `make verify` runs, 43 stacked in two worktrees, ~16 hours** (the verify storm) |

Budgets: `AO_LANE_RAM_MIB` (default **1536**) and `AO_LANE_TMP_MIB` (default
**512**), both per concurrent lane. They are conservative by design and the
ceiling is *computed from the headroom that exists right now*.

The RAM number is measurement-derived, not taste: on 2026-09-14 the aggregate
`VmRSS` of one gate's whole process **tree** was sampled every 0.5s for 420s
across sibling lanes, and the per-gate **peak** was 236 MiB (the five gates seen
peaked at 3 / 23 / 24 / 194 / 236 MiB). A lane is a *subagent plus its gate*, and
the subagent is the heavier half, so 1536 MiB (≈6× the gate-only peak) is the
budget — and it is the number that reproduces this box's observed-safe
concurrency: **19 GiB available ÷ 1.5 GiB ≈ 12 lanes**, the concurrency the board
was already running at.

The `/tmp` number is a conservative allowance, stated as such: the largest single
consumer measured under `/tmp` was a **lane's own scratch tree at 912 MiB** (with
a repo copy at 480 MiB beside it), so 512 MiB is deliberately *below* the largest
observed lane scratch. On this box it does not bind (13 GiB free ÷ 512 MiB = 26 >
the RAM-driven 12) — it is the emergency bound, not the everyday one.

### 8.2 The rules that keep the bound honest

1. **An unreadable measurement is CANNOT-ASSESS, never unbounded.** If
   `/proc/meminfo` or `/tmp` cannot be read, the ceiling is unresolved and the
   queue is **held** — a control that cannot measure does not get to say "no
   limit" (AO-GR-25: failing open is worse than no control).
2. **A directive the ceiling cannot take is HELD, not consumed.** It stays in the
   inbox and the next cycle re-resolves the ceiling against the lanes that are
   actually live. The hold names the bound that held it, so an operator tuning one
   knob can see whether that knob is the one binding.
3. **A lane that declares no file set is UNATTRIBUTABLE, and is named.** It is
   admitted — holding all undeclared work would wedge a queue whose writers do not
   declare `task.files` yet — but every hold prints the count and the names, so
   "disjoint" is never claimed for work nobody could check. An operator who wants
   the strict reading sets `AO_LANE_FILES_REQUIRED=1` and undeclared lanes are
   held instead. Neither mode is silent.
4. **A set-but-unusable knob is REFUSED**, never defaulted: `FLEET_MAX_AGENTS=zero`
   is an error, not a quiet return to 10.

### 8.3 Verify

`bash scripts/check-capacity-gate.sh` (registered in `scripts/verify.sh` and
`make lint`; `make capacity-gate` runs it alone) provokes each of the three
bounds — it builds the input that would exceed the bound, requires the excess
**held** with the holding bound named, then requires the relaxed input
**admitted**, so the two paths cannot collapse into one exit code. It asserts the
loop is wired to the gate (no bare `active >= pool` remains) and mutation-proves
both halves: a resolution that cannot bind turns the controls red, and removing
the loop's call turns the wiring assertion red.

AO-GR-24's standing condition — *"Max-agents fan-out is blocked until this check
is green: the raising change and this gate land together or not at all"* — is
satisfied by this lane: the raising change (#718) and the gate landed together.

## 9. Gate admission control (issue #724)

The operator measured **49 concurrent `make verify` runs, 43 of them stacked in
two worktrees, ~16 hours of duplicated work**. Nothing bounded them, so the gate
now admits work instead of assuming it:

* **one composite gate per worktree.** A gate holds an exclusive `flock` on a
  lock file keyed by the worktree path, so a second gate in the *same* worktree
  refuses to start and names the process that holds it. Two different worktrees
  never collide: the key is the worktree, not the machine.
* **a box-wide permit bound.** A gate must also take one of
  `AO_GATE_MAX_CONCURRENT` permit slots before it starts. No free slot means
  **PARKED**: the gate runs no check and overwrites no previous attestation.
* **release on signal and on crash.** `acquire` forks a holder that keeps the
  descriptors open, so a gate killed with `SIGKILL` — where no trap can run —
  still releases its permit. A holder killed outright leaves its record behind;
  the next gate reclaims it while **naming the owner** it took it from.

The permit store must not live in a workspace (every worktree has its own copy of
the repository, so a bound stored there would be edited per lane). It is shared,
stable, and outside every checkout:

| Env var | Default | Meaning |
|---------|---------|---------|
| `AO_GATE_LOCK_ROOT` | `${XDG_RUNTIME_DIR:-/tmp}/agent-orchestrator-gates` | the shared permit store |
| `AO_GATE_MAX_CONCURRENT` | `4` | box-wide cap on concurrent gates |
| `AO_GATE_LOCK_TTL` | `900` | seconds before a leftover record is called stale |

`/tmp` on this box is a 16 GB tmpfs that has silently truncated writes to 0
bytes, so the store verifies its own writes (a grant it cannot evidence is an
error, not a grant), and a 0-byte record is never read as an empty slot: the
`flock`, not the bytes, is the exclusion.

**The wiring is RC-8 territory (issue #559) and is NOT yet applied.**
`scripts/verify.sh` is single-writer territory, so the admission block ships as
the exact snippet below and the wiring lane inserts it. It goes immediately
after the `mode="${1:-verify}"` line in `scripts/verify.sh` — *before*
`verify_dir`/`log` are reset, so a parked gate leaves the previous attestation
untouched instead of truncating the only evidence of the last real run.

Until the snippet lands, `scripts/check-gate-lock.sh` is delivered but invoked by
no gate file, so `scripts/check-gate-coverage.sh` reports it as `uninvoked`.
Applying the snippet is what makes it wired: no baseline row is involved, and a
baseline row for it would be refused by the detector anyway.

```bash
# --- admission control (issue #724) -----------------------------------------
# One composite gate per worktree, bounded box-wide by a permit store outside
# every workspace. A gate that cannot get a permit is PARKED: it runs no check
# and it overwrites no previous attestation. Exit codes: 10 another gate holds
# this worktree, 11 the box-wide cap is reached, 12 the permit store is unusable.
bash "$root/scripts/gate-lock.sh" acquire --worktree "$root" --mode "$mode" \
  --owner-pid $$
lock_rc=$?
if [ "$lock_rc" -ne 0 ]; then
  case "$lock_rc" in
    10) echo "verify: PARKED — another gate already holds this worktree; nothing was run" >&2 ;;
    11) echo "verify: PARKED — the box-wide gate cap is reached; nothing was run" >&2 ;;
    *) echo "verify: CANNOT-ASSESS — the gate permit store is unusable; nothing was run" >&2 ;;
  esac
  exit "$lock_rc"
fi
# Only a gate that holds the lock installs the release traps: a refused gate
# must never release the lock it was refused by, and release refuses anyway when
# the lock belongs to another gate that is still alive.
trap 'bash "$root/scripts/gate-lock.sh" release --worktree "$root" --owner-pid $$ >/dev/null 2>&1' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
```

The `release` call belongs in the EXIT trap and the signal traps map to a real
exit code, so the trap runs on `Ctrl-C` (`exit 130`) and on `TERM` (`exit 143`)
and every one of those paths releases the worktree lock and the permit slot.
Until that trap exists — the admission window itself — the holder is what keeps
the bound: `--owner-pid $$` makes it watch the gate's own pid and release the
moment that process is gone, however it died, `SIGKILL` included.

`scripts/gate-lock.sh acquire|release|status` is the only interface a gate needs;
`fleet/gatelock.py` holds the mechanism and `scripts/check-gate-lock.sh` proves
the refusals — including a mutant whose exclusion always grants, so the refusal
proof cannot pass vacuously.

