---
id: ADR-0032
status: accepted
date: 2026-09-20
deciders: [owner]
req: []
supersedes: []
---

# ADR-0032: The `.fleet` loop family stays agent-orchestrator-owned, with a declared (not in-container-hardcoded) crontab

## Status

`accepted` — Owner ratified the **stay** option on 2026-09-20 (issue #1512, a
child of the offshore epic #1510).

**Numbering note:** the next free number in sequence was `0030`, which is
already cited from this repo's board for a different fleet-level record (the
"lanes hand work back uncommitted to the orchestrating lead" rule named as
`ADR-0030` by #358/#329, recorded in
[ADR-0025](ADR-0025-remote-control-transport.md) §Claims); `0031` is taken by
[ADR-0031](ADR-0031-solution-class-ladder-pin-and-class-ceilings.md). A
tree-wide grep (excluding `vendor/`, `.research/`, `.board/`) finds zero
citations of `ADR-0032`, so this record takes `0032`.

## Context

The offshore inventory (SS-MODULARITY-2026-09-20.md §4) flagged the `.fleet`
loop family as needing a **design call, not a mechanical lift**: it runs on the
fleet host, driven by `infra/fleet/entrypoint.sh` (containerized), and its
schedule was historically installed **inside the container at runtime** rather
than being a declared, auditable artifact. The question this ADR answers is
whether that loop family is agent-orchestrator's own control-plane logic (and
should **stay** here) or generic mechanical work that belongs in shared-services
as a module (and should **move**).

The loop family, verified on the current branch (`origin/master`
`2509de80`, 2026-09-20), is:

| Rung | Code | Role |
|---|---|---|
| **brain** | `fleet/brain.py` (via `fleet/brain.sh`) | dispatch/board orchestration — claims, board state, directive relay |
| **sister** (dumb terminal) | `fleet/terminal.py` (via `fleet/terminal.sh`) | the never-idle loop that runs subagents (`operator → brain → sister → subagents`) |
| **watchdog** | `fleet/watchdog.py` | respawns a missing/stale/drifted rung |
| **reconcile** | `governance/reconcile/cli.py watch` | orphan reconciliation (issue #304) |
| **prune** | `fleet/prune.py` | mailbox aging + append-only ledger rotation (issue #280) |

Runtime state (heartbeats, logs, leases, mailbox) lives in `.fleet/`; the code
lives in `fleet/` and `governance/reconcile/`.

The schedule itself is **already declared**, not hardcoded: the crontab is
generated from the tracked manifest `config/fleet-jobs.json` (issue #241),
rendered and reconciled by `fleet/cron.py` (the single writer — the installed
crontab is never hand-edited), and applied idempotently on every container
start by `infra/fleet/entrypoint.sh` (which holds no interval literal or
schedule copy of its own). The issue's Step 4 premise ("previously-hardcoded
schedule in Python") is therefore **stale** — the mechanical half it asks for
(`infra/fleet/crontab.declared` *or equivalent*) is satisfied by
`config/fleet-jobs.json`, and writing a second declaration file would violate
the single-owner discipline `fleet/cron.py` and `entrypoint.sh` already encode.

## Decision

**The `.fleet` loop family stays owned by agent-orchestrator.** It is not moved
to shared-services as a generic module.

The loop family directly implements this repo's dispatch/claim/board semantics.
`fleet/brain.py` and the sister loop are the execution rungs of
`governance/dispatch/` — they decide *what is claimed, by whom, in what order*,
and they read/write agent claims and board state. That is agent-orchestrator's
core product logic, not generic mechanical work. Moving it to shared-services
would **invert the module boundary**: shared-services would have to import
agent-orchestrator's dispatch semantics to run the loop, which is the wrong
direction — shared-services is a *consumer* of this control plane (as
`agentconsole` is consumed back), not the place its orchestration engine lives.

This matches the pattern already established for `code-indexing`'s
workstation-scoped cron (SS-MODULARITY §4: *"by design this is meant to stay
per-workstation"*) and the single-owner schedule discipline of
`infra/fleet/entrypoint.sh` (which exists precisely so that `fleet/cron.py`
stays "the one place the schedule is defined").

The **declared crontab** is `config/fleet-jobs.json` → `fleet/cron.py` →
`infra/fleet/entrypoint.sh`. No in-container hardcoded schedule remains, and no
second declaration file is introduced.

**Hosting boundary.** The loop family runs on the remote shared-services host
(`dev-elevatediq`), never on localhost. Its schedule is the declared
`config/fleet-jobs.json` manifest; a local checkout is only ever a *reader* of
that manifest, never a second installer of the crontab.

## Consequences

- **Positive.** The module boundary is preserved: shared-services stays generic
  infra, and agent-orchestrator keeps the orchestration engine it owns. No
  shared-services import of dispatch semantics is introduced.
- **Positive.** The schedule remains single-sourced and auditable — the manifest
  (`config/fleet-jobs.json`) is tracked, and `fleet/cron.py install` /
  `reconcile` are its only writers, so the declared schedule and the installed
  crontab cannot drift silently.
- **Cost.** Any future "shared-services should host the loop" requirement must
  first prove the dispatch/board semantics can be cleanly extracted — a genuine
  re-design, not a lift-and-shift. This ADR does not pre-close that door; it
  records that today the coupling is structural.
- **No follow-up.** Because the decision is **stay**, no shared-services
  follow-up issue (module design, crontab-to-timer conversion, migration plan)
  is required. Had the decision been **move**, that follow-up would be mandatory
  per issue #1512.
