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

**Core extension points (RCA-0016, the PR-queue-clearing lessons).** Some
files are natural, LEGITIMATE extension points for more than one lane in the
same wave — `fleet/watchdog.py` (a new alarm/remedy type), `fleet/cron.py` (a
new rung), and any doc a feature is expected to append its own section to
(`docs/FLEET-PARITY.md`-shaped files). "No two lanes share a file" does not
have an exception for these; it has a PROCEDURE, because three PRs collided on
exactly this shape in one queue-clearing pass (two watchdog alarm additions,
two cron rung additions, two independently-created parity docs):

- **Declare intent before starting.** A lane that expects to touch a core
  extension point says so on its issue before opening its worktree — the
  file and the kind of change (new alarm, new rung, new section), not a
  diff. A second lane claiming the same file sees this and coordinates
  instead of discovering the collision at merge time.
- **Prefer append-only additions.** A new alarm type, a new rung, a new doc
  section is added as its own block — its own function, its own dict entry,
  its own `## heading` — never interleaved into an existing block another
  lane might also be touching. Two append-only additions to the same file
  merge cleanly far more often than two edits to the same lines do.
- **Whoever lands first sets the base; the second REBASES onto it.** The
  second lane to land does not resolve a conflict blind against its own
  stale branch — it rebases its commit onto the first lane's landed change and
  re-runs `make verify` on the rebased result. A conflict resolved without
  looking at what the other lane actually shipped is how two features regress
  each other silently.
- A doc that risks being created independently by two lanes (a new
  `docs/*-PARITY.md`-shaped file) is claimed the same way as a core code
  file: declare the filename on the issue before creating it, so a second
  lane finds the claim instead of writing a byte-different duplicate.

This is lighter than a lock registry on purpose: the existing dispatch claim
(`governance/dispatch/cli.py claim`) already names the issue and lane; core
extension points just require that claim to also name the shared file, up
front, in prose a sibling lane will actually read.

**Merge order: gate-changing PRs land last** (issue #1053, RCA of the
2026-09-17 queue-clearing pass). A PR that touches a gate path
(`scripts/verify.sh`, `scripts/gate.sh`, `scripts/merge-gate.sh`,
`scripts/check-*.sh`, `scripts/gate-coverage-baseline.txt`) changes what
every later merge in the queue must satisfy, so it is sequenced after every
ready, non-gate-changing PR — never interleaved ahead of one, even if it
claimed the queue first. `scripts/pr-queue.sh` (`docs/PR-QUEUE.md`) plans this
ordering (and, opt-in, executes it) offline and by rule rather than by a
human re-deriving it under pressure each time a queue is cleared by hand.

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
   work. `scripts/check-squash-message.sh --pr <n>` runs before every `gh pr
   merge --squash` (`scripts/pr-queue.sh`); a NOT-OK verdict refuses the merge
   by name (`squash-message-would-drop-trailer`) and leaves the PR open
   (issue #1102).
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

**The wiring is APPLIED and self-applying (issue #724).** The admission block is
part of `scripts/verify.sh` itself — it is not a snippet an operator pastes, and
no gate run can skip it, because the gate IS the file that carries it. It sits
immediately after the `mode="${1:-verify}"` line and *before* `verify_dir`/`log`
are reset, so a parked gate leaves the previous attestation untouched instead of
truncating the only evidence of the last real run. **If you edit
`scripts/verify.sh`, keep that ordering**: an admission check placed after the
`.verify/` reset would let a parked gate destroy the last real attestation.

`scripts/check-gate-lock.sh` is wired into the gate of record by
`scripts/verify.sh`'s check-discovery layer (#698), so
`scripts/check-gate-coverage.sh` reports it as invoked rather than as an
`uninvoked` artifact. It proves the wiring two ways, and both can genuinely fail:
**structurally** (the prelude is present in `scripts/verify.sh` and precedes the
`.verify/` truncation, so a deleted or relocated prelude fails by name) and
**live** (a second real `scripts/verify.sh` is started in the same worktree and
must be refused by name while running zero checks and writing nothing).

The lines now in `scripts/verify.sh`:

```bash
# --- admission control (issue #724) -----------------------------------------
bash "$root/scripts/gate-lock.sh" acquire --worktree "$root" --mode "$mode" \
  --owner-pid $$
lock_rc=$?
if [ "$lock_rc" -ne 0 ]; then
  case "$lock_rc" in
    10) echo "verify: PARKED (rc 10, not a pass and not a failure) — another gate already holds this worktree; ..." >&2 ;;
    11) echo "verify: PARKED (rc 11, not a pass and not a failure) — the box-wide gate cap is reached; ..." >&2 ;;
    *) echo "verify: CANNOT-ASSESS (rc $lock_rc, not a pass and not a failure) — the gate permit store is unusable; ..." >&2 ;;
  esac
  exit "$lock_rc"
fi
# Only a gate that HELD the lock installs the release traps.
trap 'bash "$root/scripts/gate-lock.sh" release --worktree "$root" --owner-pid $$ >/dev/null 2>&1' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
```

The `release` call belongs in the EXIT trap and the signal traps map to a real
exit code, so the trap runs on `Ctrl-C` (`exit 130`), on `TERM` (`exit 143`) and
on `HUP` (`exit 129`), and every one of those paths releases the worktree lock
and the permit slot. `SIGHUP` is handled deliberately: its default action
terminates the shell immediately, so a loop that left it unhandled would skip the
release. A gate killed outright runs no trap at all; there the holder is what
keeps the bound — `--owner-pid $$` makes it watch the gate's own pid and release
the moment that process is gone, however it died, `SIGKILL` included.

`scripts/gate-lock.sh acquire|release|status` is the only interface a gate needs;
`fleet/gatelock.py` holds the mechanism and `scripts/check-gate-lock.sh` proves
the refusals — including a mutant whose exclusion always grants, so the refusal
proof cannot pass vacuously.

**Running a gate while another holds your worktree.** Query before you start:
`bash scripts/gate-lock.sh status --worktree "$PWD"` reports `HELD`, `STALE` or
`FREE`. A `STALE` record (a holder killed outright) is reclaimed by the next
`acquire`, which names the owner it took it from, so no manual clearing is
needed for that case. Do not clear a `HELD` lock to "unblock" a run: that is the
bound doing its job, and the honest response is to wait for the holder or to
raise `AO_GATE_MAX_CONCURRENT` — never to disable the lock.


## 10. The spawn envelope (issue #793)

`governance/**` is a large, well-tested surface — claim ledger, lane isolation,
lifecycle close-out, reconcile, runaway guard, capacity, and the gate admission
control of §9 — and **none of it was applied by the act of spawning**. The
remote path inlined governance as *prompt prose* inside
`fleet/terminal.py::build_prompt`, where nothing could check that a spawn had
carried it; the local path (a session subagent) shared none of it. The measured
consequence: **nine worktrees running a gate, eight of them running exactly one,
and one running twenty-six.** AO-GR-22 held for eight lanes and failed silently
for the ninth, because a rule that lives only in a document cannot refuse
anything.

**The rule of this section: a spawn carries one envelope, produced by one
producer, and a spawn that cannot present it is refused.**

* `governance/spawn/` is the single producer. `model.py` defines the versioned
  document (`spawn-envelope/v1`) and its validation; `sources.py` reads each
  field from the institution that OWNS it (the claim ledger, the minted session
  identity, the issue's epic, the capacity bound and gate permit, the attempt
  budget, the issue's own `Verify:` clause); `render.py` holds the ONE copy of
  the spawn prose; `liveness.py` decides whether a run is in flight.
* **Both spawn paths consume it.** `fleet/terminal.py::build_prompt` renders the
  document instead of restating governance, and `governance/spawn/cli.py open`
  produces the *same* document for a locally spawned subagent — so the two
  regimes converge by construction rather than by convention.
* **Refusal is a precondition, not a warning.** An envelope that cannot be
  validated is refused by name, fail-closed, with exit code **78** — deliberately
  outside the 0/1/2 tri-state, so nothing can read a refused spawn as a pass, a
  failure, or a skip. The loop refuses the spawn *before* any child exists.
* **A run in flight is the marker's own evidence.** `child_pid` that is alive, or
  a beat the run's own beater advanced — never the loop's pid, which outlives
  every run it dispatches. A heartbeat saying `idle` beside a live child is a
  **reported** contradiction, never a silent win for the marker.

The fields are fixed and versioned: `issue`, `lane`, `worktree`, `session`,
`trailer`, `claim`, `focus`, `capacity` (with the gate permit), `budget`, `gate`,
`verify`. Adding one is a schema version bump; omitting one is a refusal.

`scripts/check-spawn-envelope.sh` is wired into the gate of record by
`scripts/verify.sh`, so `scripts/check-gate-coverage.sh` reports it as invoked
rather than as an `uninvoked` artifact. It proves the wiring structurally *and*
by provocation: every required field is removed one at a time and must be refused
BY NAME; the local path is driven for real against a scratch root with its own
board and claim (admitted, then refused when the claim is absent); the fleet
prompt must EQUAL the envelope's rendering, byte for byte; a malformed envelope
must refuse the loop's spawn with its own exit code; `run_in_flight()` must not
hold the lock on a crashed run's leftover marker; and **the one-gate bound is
provoked with a real second gate** in the worktree the envelope names.

`governance/spawn/README.md` is the module's own contract; the suite
`governance/spawn/tests` is declared in `scripts/pytest-suites.txt` and named
from inside the check, so it is covered rather than merely declared.
