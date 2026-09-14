# Mechanical execution layer — concept & intent

> **Status:** intent-only spec (concept) · **Issue:** kushin77/agent-orchestrator#239 ·
> **Cross-refs:** cannibalization index [kushin77/agent-orchestrator#8](https://github.com/kushin77/agent-orchestrator/issues/8),
> [`CANNIBALIZATION.md`](CANNIBALIZATION.md) §12 · **Pillar:** `engine/` (state-machine execution)

This document is the **concept and intent** for the cheap deterministic execution
lane, the `mechanical-sme` tier. It fixes the vocabulary, the task-flow states and
the ownership boundaries so the implementing issues that follow cannot drift apart
on the same words.

**Scope of this issue (#239): concept and code only. There is NO container work
here** — no Dockerfile, no image, no compose service, no registry push. The
containerised form is a later issue at most; everything below is stated so it can
be implemented **in-process, in Python**, on this repo's existing substrate
(`engine/`, `fleet/`). If a later lane wants containers, it re-opens that decision
on its own issue rather than inheriting it from here.

---

## 1. Why a separate lane

The orchestrator already runs judged, reasoning work through the brain → sister →
subagent path. That path is expensive: it carries a model, a reasoning budget, a
worktree and a claim. A large class of work is **deterministic and mechanical**
— run a known command, move a file, poll a queue, re-run a gate, collect a result.
That work should not pay for a reasoning seat.

The mechanical lane is that seat: the cheapest deterministic executor, invoked with
a **fully-specified task** (a command plus its inputs) and no latitude to interpret
intent. It is deliberately dull. Its value is that it never needs a model to decide
what to do — the task already said it.

**Tier naming.** `mechanical-sme` is a *role*, not a model. It sits in this repo's
existing FinOps vocabulary (`flash` / `pro` / `auditor`; see
[`CANNIBALIZATION.md`](CANNIBALIZATION.md) §7): a mechanical task is dispatched to
the cheapest capable tier, and escalation stays a brain decision, never a
worker's self-chosen one.

---

## 2. Task flow — the file queue

The queue is a directory of four states. Moving a task is a rename, and a rename
inside one filesystem is atomic, so two workers cannot both "move" the same task
out of `pending/`.

| State | Meaning | Who writes it |
|---|---|---|
| `pending/` | Enqueued, unclaimed. One file per task. | The producer (brain, or any code with the queue path). |
| `claimed/` | Owned by exactly one worker for a bounded lease. | The claiming worker, via an atomic claim. |
| `done/` | Terminal success; carries the result record. | The worker that ran it. |
| `failed/` | Terminal failure; carries the error record and the exit code. | The worker that ran it. |

Rules the states imply:

- **A task is one file.** Its content is the *dispatch record* (the command to run,
  its arguments, its working directory, its FinOps block, its issue reference) —
  not the task's output. Output is separate, so a big result never rewrites the
  dispatch record.
- **Claim by atomic rename.** `pending/<id>` → `claimed/<id>`. The rename is the
  claim; whoever wins the rename owns the task. No lock file, no lock daemon.
- **A claim is a lease, not a grant.** The claimed record carries the owner id and
  a deadline. A claim whose deadline has passed is **recoverable** — it is moved
  back to `pending/` by any worker, so a killed worker cannot wedge a task forever.
  This mirrors the reclaim discipline in `governance/reconcile/`, where a stale
  holder is reconciled rather than silently trusted.
- **Terminal states are terminal.** `done/` and `failed/` are never moved back
  automatically. A failed task is re-enqueued by producing **a new task**, so the
  history of the attempt is preserved instead of overwritten.
- **Every transition is observable.** Each move appends one line to the lane's
  structured log, so "what happened to task X" is answerable without a database.

---

## 3. Pre-warmed worker pool + hot executor

Two separate ideas, often conflated:

- **The pool is pre-warmed.** A fixed number of workers are started once and kept
  alive, so a task does not pay process-start cost. The pool is a supervisor's
  responsibility: workers are re-spawned when they exit, and the pool size is
  configuration, not a hardcoded constant.
- **The executor is hot.** Within a worker, the executor loop already holds its
  imports, its queue paths and its logger; it does not re-initialise per task.
  "Hot" here means *warm state reused across tasks*, nothing more.

**Re-implemented in Python.** This is the deliberate divergence from the harvested
prior art: the leaderboard fleet's equivalent is a bash/supervisor/container stack,
and none of that is copied. Here the pool and the executor are Python modules under
`engine/`, sharing the task-flow vocabulary above. So the mechanical lane runs with
no container runtime and no extra daemon — just processes.

**Bounded, and honest about it.** The pool has a maximum concurrency; a task that
cannot be claimed because every worker is busy stays in `pending/` and is claimed
when one frees up. The pool never grows without bound to chase a backlog.

---

## 4. Entrypoint ROLE-dispatch skeleton

One entrypoint starts every worker; the **ROLE** decides what the worker is, so a
lane's shape is data, not a separate binary per role.

```
entrypoint(ROLE) ->
    resolve the role's allowed task kinds and queue directory
    warm the executor (one-time setup for this role)
    loop:
        task = claim_next()            # atomic rename from pending/ to claimed/
        if task is None:               # nothing to do
            idle()                     # a bounded wait, then loop again
            continue
        result = execute(task)         # the hot executor; deterministic
        finish(task, result)           # rename to done/ or failed/
```

Intent-level properties the skeleton must keep:

- **ROLE is the only branch.** Role-specific behaviour is chosen once, at startup,
  from the role's declared capabilities — never by sniffing task content inside the
  executor.
- **The loop never exits on idle.** Idle is a state, not a terminal condition, in
  the same way `fleet/terminal.py` never exits when the board is empty.
- **A refusal is a first-class outcome.** A task the role may not run is refused
  with a named reason and moved to `failed/` with that reason recorded — not
  silently dropped and not retried forever.
- **The skeleton is role-agnostic.** The same entrypoint runs every mechanical
  role; adding a role is adding a capability declaration, not a new script.

---

## 5. Fanout — conflict-free claim

Fanout is "one task, N workers, no double-run". The queue already gives the
primitive; the fanout rule is what keeps it honest:

- **Claim before work, exactly once.** Each worker claims its own task with the
  atomic rename. Two workers racing for one task produce one winner and one
  `None` — never two executions.
- **A fanout does not pre-assign.** The producer enqueues N tasks; it does not
  hand task K to worker K. Assignment by claim is what makes worker death
  recoverable — a pre-assigned task would die with its worker.
- **Disjoint by construction.** Each task names the files or resources it may
  touch, and the producer is responsible for enqueuing a set whose members do not
  overlap. The lane enforces single-claim; it does not arbitrate two tasks that
  both claim the same file, so "no two lanes share a file" stays the producer's
  obligation (the repo's one-issue-one-lane rule).
- **Completion is counted, not assumed.** A fanout is done when every task it
  enqueued is in `done/` or `failed/`; the producer waits on that count, not on a
  wall-clock guess.

---

## 6. Provenance & inspiration (GR-10)

The shape of this lane is **consumed prior art**, harvested read-only from a
kushin77-owned repository. That repo carries the license **"Copyright (c) 2026
kushin77. All Rights Reserved. PROPRIETARY — INTERNAL USE ONLY"** at its root — the
same owner as this repo. What is reused is therefore the **pattern and the
vocabulary**, re-implemented for this repo's Python substrate; **no source file was
copied**, so no `harvested_from` code marker applies to any file here.

| Source (repo-relative) | Asset | Verdict | What this spec takes |
|---|---|---|---|
| [`leaderboard`](https://github.com/kushin77/leaderboard) `docker/worker-fleet/entrypoint.sh` | ROLE-based entrypoint dispatch (the container's behaviour is chosen by `ROLE`) | PATTERN | §4 — one entrypoint, ROLE decides the worker's task kinds and queue. Containers are explicitly **not** adopted. |
| `leaderboard` `docker/worker-fleet/personas.yaml` | Worker-role taxonomy with per-role intervals and capabilities | PATTERN | §2/§4 — roles are declarative; a role declares its capabilities instead of hardcoding behaviour in a script. |
| `leaderboard` `docker/worker-fleet/hot-executor.Dockerfile` + `pool-sidecar/` | pre-warmed pool + hot executor, worker-side supervision | PATTERN | §3 — pre-warmed pool and hot executor as **concepts**, re-implemented in Python; the container packaging is out of scope. |
| `leaderboard` `scripts/dispatch/fanout.sh`, `agent-dispatch-{enqueue,pull,ack}.sh` | claim-based fanout queue (enqueue → pull → ack) | PATTERN | §2/§5 — the four-state file queue and claim-before-work; the queue is files here, not the harvested implementation. |
| `leaderboard` `config/fleet-jobs.json` | Declarative job catalog (which task kinds exist) | REFERENCE | §4 — task kinds come from a declared catalog, not from ad-hoc code paths. Read for shape only. |

**Paths confirmed present** in the local clone at `/home/akushnir/leaderboard`
(`HEAD` `f7bc4715`) before citing: `docker/worker-fleet/entrypoint.sh`,
`docker/worker-fleet/personas.yaml`, `docker/worker-fleet/hot-executor.Dockerfile`,
`docker/worker-fleet/pool-sidecar/`, `scripts/dispatch/fanout.sh`,
`scripts/dispatch/agent-dispatch-enqueue.sh`, `config/fleet-jobs.json`. A deeper,
pattern-by-pattern record of the same source — including the hardening patterns
harvested by the sibling #240 lane — lives in [`CANNIBALIZATION.md`](CANNIBALIZATION.md) §12.

**Explicitly not adopted from the source:** the container packaging (Dockerfiles,
compose services, sidecars, image pinning), the scheduler (`fleet.cron`), and the
supervisor/daemon topology. This issue is concept-only and container-free by
design.

---

## 7. Cross-references & next issues

- **Cannibalization index — issue #8.** The mechanical lane consumes the same
  prior art as #240; its patterns are recorded in [`CANNIBALIZATION.md`](CANNIBALIZATION.md)
  §12 so both lanes cite one index rather than two drifting descriptions.
- **Full review.** The wider review the issue points at lives at
  `~/.research/leaderboard-mechanical-fleet-cannibalization.md` (gitignored, not
  committed) — this document is the committed distillation.
- **The implementation is a separate issue.** This spec does not itself add the
  queue, the pool or the entrypoint; it fixes the vocabulary and the states those
  issues implement.

---

## 8. Intent-level acceptance criteria (for the implementing issues)

These are the criteria the *concept* must satisfy; the implementing issue restates
them as executable checks.

1. The four queue states (`pending/`, `claimed/`, `done/`, `failed/`) exist, and a
   task's transition between them is an atomic rename — a test provokes two
   concurrent claims and asserts exactly one winner.
2. A claim carries an owner and a deadline; a claim past its deadline is
   recoverable to `pending/` — a test kills a claimant and asserts the task is
   re-claimable.
3. The pool is pre-warmed and bounded: its size is configuration, a worker that
   exits is re-spawned, and concurrency never exceeds the configured maximum.
4. The executor is hot: a worker runs more than one task without re-initialising
   its queue paths, imports or logger.
5. One entrypoint serves every role, and the role is selected from a declared
   capability record — adding a role adds no new script.
6. A refused task lands in `failed/` with a named reason, and is not retried
   forever.
7. The lane runs with **no container runtime and no Dockerfile** — the whole lane
   is Python processes.

**Provenance (GR-10):** `harvested_from:` `kushin77/leaderboard`
(`docker/worker-fleet/entrypoint.sh`, `docker/worker-fleet/personas.yaml`,
`docker/worker-fleet/hot-executor.Dockerfile`, `docker/worker-fleet/pool-sidecar/`,
`scripts/dispatch/fanout.sh`, `scripts/dispatch/agent-dispatch-enqueue.sh`,
`config/fleet-jobs.json`) — pattern/REFERENCE only, kushin77 proprietary /
internal use only, no code copied.
