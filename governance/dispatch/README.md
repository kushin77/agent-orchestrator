# governance/dispatch — claim-time issue-order enforcement (issue #157)

The chronological-dispatch rule lives in `AGENTS.md` (golden rule 14),
`docs/GOVERNANCE.md` (section 8) and `docs/EXECUTION-PLAN.md` (section 5), and
`scripts/check-chronological-dispatch.sh` gates that those documents keep
declaring it. That is a **declaration** gate.

This package is the **behavioural** half. It refuses a claim on an issue that is
not the next step in the active dependency chain, so the board behaves like an
execution plan instead of a backlog to browse.

## The rule, mechanically

An issue is claimable only when one of these holds:

| Reason | Meaning |
|---|---|
| `next-in-milestone` | It is the frontier of the active milestone: the lowest-numbered open, unblocked, unclaimed **non-epic** issue in that milestone. |
| `child-of-claim` | It declares `Parent: #n` and the agent already holds #n. |
| `successor-of-claim` | It declares `Blocked-by: #n` and the agent already advanced #n. |
| `brain-directed` | A brain directive recorded in `.fleet/sent` names exactly this issue (`claim --directive <id>`). The brain is the chain. |

Everything else is refused with a reason: `unknown-issue`, `issue-closed`,
`blocked`, `already-claimed`, `epic-not-workable`, or `no-chain-edge` — the last
being kanban scavenging. Epics are never claim targets: an epic closes with its
children.

## Claim protocol

```bash
python3 governance/dispatch/cli.py status                        # active milestone + frontier
python3 governance/dispatch/cli.py eligible --issue 139 --agent me
python3 governance/dispatch/cli.py claim --issue 139 --agent me --lane knowledge-index
# ...do the work, open the PR...
python3 governance/dispatch/cli.py release --issue 139 --agent me
```

Exit codes follow the repo tri-state convention: `0` OK, `1` refused/NOT-OK,
`2` CANNOT-ASSESS (for example the snapshot is missing).

* **Claim record** — one JSON object per line in the append-only, tracked
  `.board/claims.jsonl`: agent, lane, base commit, reason, snapshot hash,
  timestamp and TTL. The record is the evidence that the claim was order-checked.
* **Single-claim lock** — `.board/locks/<issue>.lock` is created
  `O_CREAT|O_EXCL`, so a second claim on an in-flight issue fails loudly instead
  of two agents duplicating work. A claim whose TTL elapsed may be taken over,
  so a dead agent cannot wedge the chain. Lock files are runtime state and are
  ignored by git.
* **Board snapshot** — `.board/snapshot.json` is the committed board state the
  gate audits against. Refresh it with
  `python3 governance/dispatch/cli.py snapshot --from-github` (the only
  network-touching path in this package); the gate itself is offline.

## Chain markers

Dependency edges are declared in the issue body, so a chain is explicit rather
than guessed:

```text
Parent: #138        Part-of: #138        (same edge, alternate spelling)
Blocked-by: #139, #140
```

An issue with no markers is still reachable as its milestone frontier; it is
never reachable merely because it is visible.

## The gate

```bash
bash scripts/check-issue-claims.sh      # 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
```

`make issue-claims` runs the same check; it is part of `make verify` and
`make lint`.

The audit re-derives the **time-stable** invariants — record schema, declared
chain edges, milestone membership, blockers, the single-claim lock, release
pairing, epics, expired-but-unreleased claims. Strict frontier *ordering* is
enforced live at claim time by `order.eligible`, because a historical frontier
cannot be recomputed from today's snapshot.

The audit always runs its own mutants first: scavenged claim, blocked claim,
closed claim, epic claim, duplicate claim, double release, unsupported reason
and a malformed record must all be **rejected**, while a valid frontier claim
and a valid child claim must be **accepted**. If the audit cannot fail, the gate
fails (GR-12 / AO-GR-19: a check that cannot fail is a formality).

## Layout

| File | Role |
|---|---|
| `model.py` | Issue / Snapshot / Eligibility / ClaimEvent dataclasses and reason codes |
| `snapshot.py` | Snapshot build, load, hash, chain-marker parsing, `gh` fetch |
| `order.py` | Eligibility rules and frontier/milestone resolution |
| `claims.py` | Ledger, single-claim lock, TTL take-over, audit, self-control |
| `cli.py` | `status` / `eligible` / `claim` / `release` / `snapshot` / `audit` |
| `tests/` | Eligibility, lock, TTL, audit and anti-formality controls |
