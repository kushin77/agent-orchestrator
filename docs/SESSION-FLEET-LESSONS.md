# Session-fleet lessons register

Register only — no code, no gate. This file records the owner's six lessons from
taking the A2A model live (the file-mailbox + push/wait/trigger decision) and the
seven-item enterprise-scale hardening register, each item mapped to its owning
issue. Tracked by issue #180.

## Six lessons learned

1. **A dumb terminal needs a pulse.** Transport is not a loop; the first live
   steering deadlocked 140 s with two directives queued because the sister had
   no listener (#175). Lesson: ship the listener with the transport, test the
   *loop* not the channel.

2. **Consume-on-report keeps the queue honest.** Answered directives left in the
   inbox look like outstanding work; `report` must retire what it answers.

3. **Every pair of enforcement surfaces needs an explicit edge.** The channel
   directs, the claim gate refuses; the `claim --directive` integration (#178)
   is the bridge. Any future enforcement pair needs its bridge designed, not
   discovered.

4. **Declared is not verified.** The sister's runtime mode (DSv4FNone) has no
   introspection surface; the runbook needs a runtime attestation artifact
   (#166).

5. **Shared temp paths and shared gate files collide.** Two sessions derived the
   same worktree path; two lanes adding gates to `Makefile`/`verify.sh`
   conflict. Unique worktree names + explicit `git add` + keep-both merge
   resolution.

6. **The cheapest capable tier was right.** `flash`/`none` steered the sister
   through a real bootstrap with zero escalations.

## Enterprise-scale hardening register

What a true elite / SaaS / enterprise version still needs. Every owning-issue
number below is verified against the committed board snapshot
(`.board/snapshot.json`) at this lane's base commit; the number is the issue
whose subject owns the gap.

| # | Gap | Owning issue |
|---|-----|--------------|
| 1 | Transport promotion — file mailbox is single-host localhost mechanics (GR-21); multi-host needs the M9 A2A layer (HTTP/SSE + Agent Cards). | #102–#109 (transport/gateway: #105) |
| 2 | Trust — signed, authenticated directives; the mailbox trusts the filesystem. | #107 |
| 3 | Brain supervision — no brain-side daemon, latching alerts, or escalation path when a directive stalls. | #184, #190 |
| 4 | Queue QoS — priorities, deadlines, retries/backoff, per-directive budget caps. | #164 (partial — budget/tier only) |
| 5 | Observability — correlation ids exist but nothing aggregates latency/cost per directive chain. | #108 |
| 6 | Tenant isolation — one repo = one org today; the identity pillar must scope fleets per tenant. | identity pillar ([ADR-0007](decision-records/ADR-0007-identity-rbac-contract.md); #35, #30) |
| 7 | Durable brain — the brain is a single session; enterprise needs a durable orchestrator + state machine. | #106 |

GR-21 (localhost mechanics, no network) and the session-fleet transport decision
are recorded in [`ADR-0011`](decision-records/ADR-0011-session-fleet-transport.md);
precedence is [`../AGENTS.md`](../AGENTS.md).

## Platform RCA register 2026-11-06 (PAP-22)

This register lists 15 root-cause classes from the Paperclip platform. PAP-22 tracks it.
Detector status comes from `git ls-tree` on `origin/master` in paperclip-live.
No detector below is on `origin/master`.
Lane pc-run-health holds `run-health.sh` and `verify-declared-timeouts.sh`.
Lane pap-31-process-lost adds the run-health rule `pid-untracked`.
The run-health rules in that lane are agent-error, lost-run, no-run, pid-untracked and queued-old.
Lanes pc-run-id-header, pc-claude-api-first and pc-flow-keeper hold server and reconciler fixes, not detectors.
"No script found" means no detector script exists on any paperclip lane.

| # | Class | Detector | Template rule | Lesson |
|---|-------|----------|---------------|--------|
| 1 | Default adapter timeout of 300 s killed Claude runs. | `verify-declared-timeouts`: on lane pc-run-health, not landed. | Every agent declares `timeoutSec` and `graceSec`. | A default limit is a hidden limit. Declare it. |
| 2 | Continuation re-woke waiting in_progress issues (148 and 137 wakes). | `loop-same-result`: no script found. | Waiting means in_review or blocked. | An issue that waits is not in_progress. |
| 3 | Bulk comments caused wake storms. | `queued-old` and queue depth: on lane pc-run-health, not landed. | No bulk comments. Put notes in descriptions. | Each comment wakes an agent. Write one note, not many. |
| 4 | A shared agent cwd caused file collisions. | Reconciler shared-cwd check: no script found. | One cwd for each agent. | Two writers in one tree corrupt each other. |
| 5 | Hermes agents acted as board (no run credential). | Comment author is board while a run is active: no script found. | Every API call carries the run credential. Never invent a run id (class C17: HTTP 500 from `orc-252-check`). | A call without a credential has the wrong identity. A made-up id is worse. |
| 6 | The qdrant-memory put call was broken. | Memory write probe: no script found. | Memory health is a run-health rule. | A broken write path stays silent until a probe writes. |
| 7 | Issues went done before merge. | Done without a merge SHA: no script found. | Done means merged, with a SHA. | Done is a claim. The SHA is the proof. |
| 8 | in_review had no owner. | Review SLA of 60 minutes: no script found. | in_review means pushed and handed to the landing path. | A review state with no owner never ends. |
| 9 | A stale dirty primary checkout fed pr-queue. | `primary-fresh`: no script found. | Tools never read a primary working tree. | A primary tree holds local state, not landed state. |
| 10 | The reconciler reverted live fixes (budget 80000 to 2000). Two writers did it: reconcile-agents and sync-and-up.sh step 9. | Declared-versus-live drift: no script found. | A live fix lands in the declaration in the same hour. | Two writers need one source. The declaration wins. |
| 11 | The KVM train stuck on a stale memo from a deleted tree. | Train stopped for more than 2 hours: no script found. | The memo key uses the current inputs. | A memo keyed on old inputs repeats an old answer. |
| 12 | bin/land read "0 CANNOT-ASSESS" as a failure. | Refuse-and-pass controls: no script found. | Read verdict tokens. Never search for words. | A count of zero contains the word it counts. |
| 13 | Several full verifies at once starved the host. A nested full verify also recursed (fix fceb6126). | Host lock contention and the nested-verify process tree: no script found. | One heavy gate for each host (ADR-0122 lock). Tests use fixtures, never the real gate they belong to. | A gate that runs its own gate loops. Fixtures break the loop. |
| 14 | process_lost on live hermes_local runs (PAP-31). Adapter 0.2.0 calls `runChildProcess` without `onSpawn`, so processPid is null. | Run-health rule `pid-untracked`: on lane pap-31-process-lost, not landed. | Every local adapter records processPid. | A run with no pid looks dead. Record the pid at spawn. |
| 15 | Company resume did not re-wake cancelled work (C16). | Agents with todo, in_progress or in_review issues sit idle after a resume: no script found. | Wake the holders after a resume. | A resume restores state. It does not restore wakes. |

## Related

- #175 — close the steering loop: sister-side listener + consume-on-report
- #178 — brain directives are chain edges: `claim --directive` integration
- #166 — session bootstrap + runbook: runtime attestation artifact
- #141 — enforce RCA and lessons capture
- #180 — this issue (the register itself)
