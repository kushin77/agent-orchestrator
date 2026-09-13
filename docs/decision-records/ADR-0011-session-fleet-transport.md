---
id: ADR-0011
status: accepted
date: 2026-09-13
deciders: [owner]
req: []
supersedes: []
---

# ADR-0011: Session-fleet transport — file mailbox now, A2A as the graduation target

## Status

`accepted` — ratified by issue #161 (milestone M26, epic #160). This record is
referenced by the transport it decides: [`channel.py`](../../fleet/channel.py)
cites it in the module docstring, and the topology it carries is frozen in
[`fleet/CONTRACT.md`](../../fleet/CONTRACT.md).

## Context

M26 turns this repo into the operating model the owner described: a **brain**
session (advisor, maximum DeepSeek vPro) that steers a **fleet brain** sister
session (DeepSeek v4.1 Flash, thinking off, a dumb terminal) that spawns
epic-focused **subagents**. The steering loop had to pick a transport before the
contract could be written, because the transport is what the trust rules are
enforced *at*.

Three candidates were on the table.

1. **A2A (the product protocol).** The natural end state: this repo's M9
   (#101–#109) is the agent-to-agent product layer, and the hub carries the
   governance contract the fleet runs under. But M9 has not shipped, and the
   hub's current M9 in `vendor/CMR` is GitHub full-surface governance, not an
   A2A wire contract — so there is no implemented A2A endpoint to steer *this*
   repo's live sessions through today. Choosing it now would mean the contract
   is enforced by an interface that does not exist yet.
2. **File mailbox (localhost).** Directories on the operator's own machine
   (`.fleet/{inbox,sent,outbox,done}`) plus a CLI that validates and moves
   messages. GR-21 (localhost mechanics, no network) and GR-15 (no GitHub
   Actions; automation is code-native) both point here: it needs no daemon, no
   credential, and no console click, and a human can read every message with
   `ls` and `cat`.
3. **Engine state machine.** The durable orchestration engine (`engine/`,
   phase 3) is the right home for long-running, multi-epic state with retries and
   recovery — but adopting it as the *steering* transport would require the
   engine to already be able to host a session's inbox, which is a larger
   change than a session-to-session order channel justifies.

Two operational facts pushed the decision. First, the operator described the
behaviour that was actually wanted: *sessions push tasks, wait, and get
triggered on completion* — a blocking wait with a completion trigger, not a
queue the brain polls. Second, the prior art in the fleet is unambiguous about
where coordination lives: `leaderboard/docs/LEADERBOARD_PROTOCOL.md` is a
file-and-directory protocol (per-session row files, one worktree per session, a
reporter script that *computes* status), and `leaderboard/docs/CTO_OVERLAY.md`
governs layers that run inside the repo's own checkout. Neither needs a broker.

## Decision

**The file mailbox is the transport of record for the M26 session fleet.** The
brain → sister → subagent loop moves messages through
`.fleet/{inbox,sent,outbox,done}` using `fleet/channel.py`, and the loop is
**push → wait → completion trigger**: `send` validates and queues a directive,
`wait` blocks the brain on the message id or `correlation_id`, and the executor
`report`s a correlated `ack`/`result` that wakes the waiter and consumes the
directive. Trust is enforced at the boundary — `channel.validate` refuses
out-of-contract traffic before anything is written.

**A2A remains the graduation target, not a second implementation.** When this
repo's M9 agent-to-agent layer ships, the same contract (roles, vocabulary,
envelope, trust rules) is expected to ride it; the contract is written at the
level the two transports share, and `fleet/CONTRACT.md` §0 names which artifact
is authoritative for what so the migration swaps a transport rather than
rewriting a contract.

**The engine state machine is deferred, not rejected.** Multi-epic durability
(retries, recovery, replay across sessions) stays with `engine/`; that is a
layer *above* the steering channel, and adopting it prematurely would couple the
contract to an execution substrate it does not need.

## Consequences

- **Positive:** the transport is enforceable today with no network, no daemon
  and no credential (GR-21); the trust rules are checked by code the gate
  already exercises (`scripts/check-fleet-channel.sh` mutants); every message is
  human-readable on disk, so an operator can audit the fleet without a console;
  and the ack/result correlation gives the brain a real completion trigger
  instead of a poll loop.
- **Negative:** it is single-host. Two sessions on different machines cannot
  use it, and there is no delivery guarantee beyond the filesystem — a directive
  is only answered if a listener is running, which is exactly what a timed-out
  `wait` means. It will not scale to an untrusted or remote peer, which is why
  A2A is the named target rather than an optional extra.
- **Neutral:** the mailbox is runtime state and stays gitignored
  (`.fleet/{inbox,sent,outbox,done}/`), while the contract, the envelope schema
  and the standing directive remain tracked artifacts, so a clean clone can
  still discover its standing orders.
- **Follow-ups:** when M9's A2A layer lands, a new ADR supersedes this one
  rather than editing it; the dispatcher (#163) and the FinOps chooser (#164)
  build on this transport without extending it; the pilot (#167) is the first
  end-to-end proof of the loop.
