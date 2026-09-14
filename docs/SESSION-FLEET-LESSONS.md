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

## Related

- #175 — close the steering loop: sister-side listener + consume-on-report
- #178 — brain directives are chain edges: `claim --directive` integration
- #166 — session bootstrap + runbook: runtime attestation artifact
- #141 — enforce RCA and lessons capture
- #180 — this issue (the register itself)
