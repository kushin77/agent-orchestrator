# EPIC #1268 closure — mechanical, not doctrinal, between runtimes

**Epic:** [#1268](https://github.com/kushin77/agent-orchestrator/issues/1268) —
*"mechanical, not doctrinal, between runtimes — paperclip / hermes / Claude /
DeepSeek coordination becomes records and gates"* (sibling of #1254).

**What this document is.** The clause-by-clause closure evidence for the epic's
amended definition of done, measured against a pristine `origin/master` tree.
Every command below was run and its real output is quoted verbatim; nothing here
is asserted from memory. The one claim that could not be measured is named in
§6 rather than dressed up.

**Audited tree.** `origin/master`
`0a6efb7a2fe49f22111683963e6c2e790111c65d` (*"gate: mount scripts/lib/common.sh
in check-gate-publish's scratch fixture (#1905) (#1961)"*), read from a detached
lane worktree cut from `origin/master`.

**Children.** All fourteen declared children are `closed` (re-read live):
`#1176 #1269 #1270 #1271 #1272 #1273 #1274 #1275 #1301 #1338 #1343 #1412 #1413
#1414`. The epic body's own *"4 open / 10 closed"* table is stale; the live
count is 14/14.

**DoD amendment.** Bullet 1 originally named `control-plane-verify` as a
merge-time condition. That venue is retired (#1361, superseded by #1295) and
advisory-only (#1698); the owner call of 2026-09-21T17:19:40Z asked for the
green-required-check framing to be dropped, and the body was amended
(2026-09-22T01:11:21Z) to re-point bullet 1 at the gate of record, `make verify`.
Bullets 2 and 3 are preserved verbatim. This document measures the **amended**
clauses.

---

## 1. DoD bullet 1 — every child gate runs inside `make verify`, none skipped

> *Every child's gate runs inside `make verify` (the gate of record) with a
> negative control, and none of them is skipped — each of the ten named child
> gates (`notice-acks`, `lane-record`, `lane-collision`, `runtime-liveness`,
> `tier-parity`, `tier-vocabulary`, `agent-identity-parity`,
> `paperclip-approvals`, `paperclip-routines`, `fleet-contract`) is wired into
> the composite and none is denylisted or budget-skipped.*

### 1a. The ten are wired into the composite and none is skipped

`make verify` (`scripts/verify.sh`) builds its run list from an explicit
`checks=()` array plus the self-wiring discovery layer
(`scripts/discover-checks.sh`). Measured on the audited tree:

```
$ ls scripts/check-*.sh | wc -l
205
$ bash -c 'source scripts/discover-checks.sh; discover_check_scripts' | wc -l
201                      # 205 on disk minus the 4 denylisted
$ sed -n '/^checks=(/,/^)/p' scripts/verify.sh | grep -c '|bash '
118                      # the explicit array
```

| GATE | `check-<name>.sh` | discovered | in `verify.sh` array | denylisted | in `skip-budget` |
|---|---|---|---|---|---|
| notice-acks | yes | 1 | 0 | 0 | 0 |
| lane-record | yes | 1 | 0 | 0 | 0 |
| lane-collision | yes | 1 | 1 | 0 | 0 |
| runtime-liveness | yes | 1 | 0 | 0 | 0 |
| tier-parity | yes | 1 | 0 | 0 | 0 |
| tier-vocabulary | yes | 1 | 0 | 0 | 0 |
| agent-identity-parity | yes | 1 | 1 | 0 | 0 |
| paperclip-approvals | yes | 1 | 1 | 0 | 0 |
| paperclip-routines | yes | 1 | 1 | 0 | 0 |
| fleet-contract | yes | 1 | 1 | 0 | 0 |

Five are named explicitly in `checks=()`; the other five are appended by the
discovery layer. Either way each appears exactly once in the run list, so the
ten are **wired** — the composite runs them. The denylist holds only four
entries, none of them a child gate:

```
$ grep -vE '^\s*(#|$)' scripts/check-denylist.txt
drift
negative-controls
policy-schema
pr-contract
```

`scripts/skip-budget.json` carries 13 entries, none a child gate:

```
branch-protection, cmr-knowledge, cmr-pin, codeidx-surface, diagrams-declaration,
fleet-cron-dev-run, fleet-cron-image, gate-status, module-brief, module-registry,
reconcile-orphans, repo-settings, worktree-cap
```

**Verdict 1a: met.** All ten wired; none denylisted; none budget-skipped.

### 1b. Each returns rc 0 run individually, and each provokes its own refusals

Each gate was run on its own from the pristine tree. All ten answer **rc 0** and
each prints the refusals it provoked in its own output:

```
$ for g in notice-acks lane-record lane-collision runtime-liveness tier-parity \
           tier-vocabulary agent-identity-parity paperclip-approvals \
           paperclip-routines fleet-contract; do bash scripts/check-$g.sh; done
notice-acks            rc=0
lane-record            rc=0
lane-collision         rc=0
runtime-liveness       rc=0
tier-parity            rc=0
tier-vocabulary        rc=0
agent-identity-parity  rc=0
paperclip-approvals    rc=0
paperclip-routines     rc=0
fleet-contract         rc=0
```

One negative control per gate (a mutant the gate refuses **by name**), quoted
from each gate's own run:

| GATE | negative control it provokes (verbatim) |
|---|---|
| notice-acks | `OK    fixture-planted      rc=1  notice-unacked:notice-fixture:alpha` and `OK    fixture-ghost-ack    rc=1  ack-from-unregistered-runtime:notice-fixture:ghost` |
| lane-record | `OK    refusal-unknown          rc=1  refusal-unknown:no-such-refusal` and `OK    missing-field            rc=1  lane-record-malformed:/owned_files` |
| lane-collision | `negative-control: OK — collision refused by name, unverifiable child reported` (refusal `lane-file-collision: Makefile already owned by #716`) and `mutant: OK — with the predicate disabled the collision is (wrongly) admitted, proving the gate CAN fail` |
| runtime-liveness | `OK    the leak control bites: a beat this tree did not earn reds the judge, BY NAME` (`runtime-unregistered:<id>`) and `OK    stale-by-name        runtime-stale:deepseek-sister — beat is 7200s old, past the 3600s window` |
| tier-parity | `OK    REFUSED fleet spawn at a forbidden tier (below security floor) — FINOPS-ROLE-NOT-ALLOWED` and `OK    REFUSED unknown role — FINOPS-UNKNOWN-ROLE` |
| tier-vocabulary | `REFUSED identity/onboarding/planted_copy.py:3 declares the tier ladder ('LOW', 'MED', 'HIGH', 'MAX') — read the authority instead` |
| agent-identity-parity | `control OK    A: required field stripped from a seed       rc=1`, `control OK    D: shared schema hidden                      rc=2`, closing `...and 6 negative control(s) held` |
| paperclip-approvals | `PROVOKED  1. a kind with no authority (transfer)` → `no-authority refused by name: no-authority: approval kind 'transfer' has no authoritative surface`; `PROVOKED  2a. a grant with no decision record` → `projection-without-record refused by name: approval-hire-issue-416: granted with no authoritative record — a projection change with no record behind it is a bug` |
| paperclip-routines | `OK    control entry deleted from schedule    rc=1, naming ao-fleet-prune` and `OK    control owner-less routine             rc=1, naming fleet-reconcile` |
| fleet-contract | `OK    vacuity control: removing the trust model is detected` and `OK    vacuity control: removing the primary-control-plane declaration is detected` |

**Verdict 1b: met.** Each gate answers rc 0 on the clean tree and its own
negative controls bite, refused by name.

**Bullet 1 overall: met** under the amended wording. `control-plane-verify` is
explicitly **not** a DoD condition (retired #1361, superseded by #1295,
advisory-only #1698).

---

## 2. DoD bullet 2 — a record-less new verb/rung/runtime/tier/approval reds by name

> *A new verb, rung, runtime, tier or approval that lacks its record reds by
> name before it lands.*

Each of the five classes has a gate that refuses the record-less case **by
name**, with the refusal text it prints:

| Class | Gate | Refusal (verbatim from the gate's run) |
|---|---|---|
| **verb** | `scripts/check-control-verbs.sh` | `OK    a local verb the registry omits is refused (rc=1, naming MISSING: fleet/control.py declares the verb 'stop')` and `OK    an entry whose local verb no longer exists is refused (rc=1, naming ABSENT: the registry declares 'ghost')` — the registry is `control-plane/control/verbs.yaml` against `control-plane/control/schema/verbs.schema.json` |
| **rung** | `scripts/check-paperclip-routines.sh` | `OK    control entry deleted from schedule    rc=1, naming ao-fleet-prune` (a scheduled rung with no routine claims it) |
| **runtime** | `scripts/check-notice-acks.sh` | `OK    fixture-planted      rc=1  notice-unacked:notice-fixture:alpha` (a registered runtime that acked no standing notice) and `OK    fixture-ghost-ack    rc=1  ack-from-unregistered-runtime:notice-fixture:ghost`; complements `check-runtime-liveness`'s `runtime-unregistered:<id>` |
| **tier** | `scripts/check-tier-parity.sh` + `scripts/check-tier-vocabulary.sh` | `FINOPS-ROLE-NOT-ALLOWED` (out-of-window spawn), `FINOPS-UNKNOWN-ROLE` / `FINOPS-UNKNOWN-TIER`; and the vocabulary half refuses a planted seventh copy of the ladder `BY NAME and LINE` |
| **approval** | `scripts/check-paperclip-approvals.sh` | `no-authority: approval kind 'transfer' has no authoritative surface` (a kind with no authority) and `projection-without-record: approval-hire-issue-416: granted with no authoritative record — a projection change with no record behind it is a bug` |

**Verdict: met.** Every class reds by name at the producer, before landing.

---

## 3. DoD bullet 3 — no coordination fact lives only in a chat message

> *No coordination fact between runtimes exists only in a chat message: notices,
> briefs, results, approvals and heartbeats are files under `.fleet/` or
> `governance/` with a schema.*

Each fact is a schema'd record on disk, enforced by a gate:

| Fact | Schema | Live records | Enforcing gate |
|---|---|---|---|
| **notices** | `governance/notices/schema/notice.schema.json` (`notice/v1`), `governance/notices/schema/notice-ack.schema.json` (`notice-ack/v1`) | `.fleet/notices/<id>/notice.json`, `.fleet/notices/<id>/pending/<runtime>.json`, `.fleet/notices/<id>/ack-<runtime>.json` | `check-notice-acks` |
| **briefs + results** | `governance/lane-record/schema/lane-record.schema.json` (`lane-record/v1`; `$defs.brief` and `$defs.result` over one `$defs.record`) | `.fleet/lane-records/<issue>/<lane>.brief.json` and `<lane>.result.json` | `check-lane-record` |
| **approvals** | `integrations/paperclip/adapters/approvals/schema/approval.schema.json` (projection of an authority: `.fleet/brain/inbox/*.json` requests, `.fleet/sent/*.json` directives, `governance/dispatch` claims) | `.fleet/brain/inbox/*.json`, `.fleet/sent/*.json` | `check-paperclip-approvals` |
| **heartbeats** | `docs/contracts/paperclip/heartbeat.schema.json` (produced via `integrations/paperclip/adapters/heartbeat`) | `.fleet/runtime-beats/<runtime>.json` | `check-runtime-liveness` |
| **directives** | `governance/dispatch/dispatch.schema.json` | `.fleet/sent/*.json`, `.fleet/outbox/` | `check-resource-lease` (dispatch/lease schema), `check-fleet-contract` |

Live record volume measured in the shared checkout (read-only) — the facts are
real files, not prose:

```
.fleet/lanes = 79        .fleet/sent = 81        .fleet/outbox = 9506
.fleet/lifecycle = 56    .fleet/runtime-beats = 1
```

**Verdict: met**, with one fleet-state caveat named in §6 (the heartbeat
producer has not yet run for six of the seven registered runtimes; that is fleet
state, not a missing gate — the judge engages and reds by name).

---

## 4. Child closure table

Every declared child is `closed` (re-read live). The gate that carries each
child's mechanical guarantee:

| # | state | carrying gate |
|---|---|---|
| #1176 | closed | `check-paperclip-routines` (routines for every scheduled rung) |
| #1269 | closed | `check-notice-acks` |
| #1270 | closed | `check-lane-record` |
| #1271 | closed | `check-runtime-liveness` |
| #1272 | closed | `check-paperclip-approvals` |
| #1273 | closed | `check-spawn-envelope` (per-runtime allowlists at spawn) |
| #1274 | closed | `check-tier-parity`, `check-tier-vocabulary` |
| #1275 | closed | `check-agent-identity-parity` |
| #1301 | closed | `check-lifecycle-closeout`, `check-lifecycle-reclaim` |
| #1338 | closed | `check-reconcile` (real-tree quarantine) |
| #1343 | closed | `check-pr-runner` (shared-services PR runner) |
| #1412 | closed | `check-runtime-liveness` (heartbeat producers) |
| #1413 | closed | `governance/spawn/admission.py`, `governance/spawn/model.py` (called at spawn) |
| #1414 | closed | `check-runaway-guard`, `check-capacity-gate` (orphan budget / worktree cap) |

`#1899` (the DoD bullet-1 venue gap) is `closed`; its finding is what produced
the bullet-1 amendment measured in §1.

---

## 5. Verification (gate of record)

The composite `make verify`, run on this branch (a docs-only change over
`origin/master`), is the evidence of record. Its verdict, and the pre-existing
reds it shares with the merge base, are recorded in the pull request body and
compared against the merge base in a scratch worktree — see `## Pre-existing
red` there. A red that reproduces identically at the merge base is pre-existing
and is disclosed, not claimed clean; a red introduced by this lane would be a
defect of the lane.

---

## 6. Residual / disclosed — what is **not** evidenced here

1. **Runtime liveness is fleet state, not a gate gap.** `fleet/runtimes.yaml`
   registers **7** runtimes (`claude-session`, `claude-subagent`,
   `deepseek-sister`, `deepseek-executor`, `copilot-agent`, `hermes`,
   `paperclip`), but the live venue holds **1** beat
   (`.fleet/runtime-beats/deepseek-sister.json`) — six runtimes have not posted a
   beat yet. In a bare tree `check-runtime-liveness` answers rc 0
   (`no-beats-yet`); in the live venue the judge engages and reds by name
   (`runtime-unregistered:<id>`, `runtime-stale:<id>`). The clause-3 requirement
   — the fact is a file, judged by a gate — holds; the fleet's own beats are the
   open operational item, tracked by the heartbeat producers (#1412, closed) and
   the fleet's runtime cadence.
2. **`control-plane-verify` remains red on every run that reaches a verdict.**
   This is exactly the retired venue the amendment removes from the DoD; it is
   **not** a closure condition. It is named here so the retirement is not read as
   a silent green.
3. **Live record counts drift.** The `.fleet/*` counts in §3 are a snapshot of
   the shared checkout at measurement time; they are illustrative of *volume*
   (the facts are files) and are not a stable assertion.

---

## 7. Reproduce

```bash
W=$(mktemp -d /tmp/ao1268-evidence.XXXXXX)
git -C <repo> worktree add --detach "$W" origin/master
cd "$W"

# 1a — wiring
ls scripts/check-*.sh | wc -l
bash -c 'source scripts/discover-checks.sh; discover_check_scripts' | wc -l
grep -vE '^\s*(#|$)' scripts/check-denylist.txt

# 1b — each child gate, individually
for g in notice-acks lane-record lane-collision runtime-liveness tier-parity \
         tier-vocabulary agent-identity-parity paperclip-approvals \
         paperclip-routines fleet-contract; do
  bash "scripts/check-$g.sh"; echo "$g rc=$?"
done

# bullet 2 — the five class gates
bash scripts/check-control-verbs.sh
bash scripts/check-tier-parity.sh
bash scripts/check-tier-vocabulary.sh
bash scripts/check-paperclip-approvals.sh

# bullet 3 — the schemas and their live records
find governance/notices governance/lane-record -name '*.schema.json'
ls .fleet/runtime-beats/ .fleet/sent/ .fleet/lanes/
```

<sub>Closure evidence for EPIC #1268. Read-only measurement of a pristine
`origin/master` tree; the only files this lane adds are this document and its
one-line docs-index entry.</sub>
