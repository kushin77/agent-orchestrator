# One declared policy for every fleet lease and TTL (issue #322)

The fleet honours a set of timings that are **not independent**: each one is a
lease or a TTL on an actor the others watch, so the *relationships* between them
are as load-bearing as the numbers themselves. This note is the architecture
record for `governance/policy/lease.py` — the single place they are declared.

## The gap this closes

Before #322 the fleet declared its timings in four unrelated modules, and the
relationships between them were unstated:

| Timing | Declared in (before) | Value |
|--------|----------------------|-------|
| rung heartbeat — a beat older than this means the loop died | `fleet/channel.STALE_HEARTBEAT_SECONDS` | 120s |
| session heartbeat — how often a lane refreshes its beat | `governance/reconcile/heartbeat.DEFAULT_BEAT_SECONDS` | 60s |
| session / reconcile TTL — a beat older than this is an orphan | `governance/reconcile/heartbeat.DEFAULT_TTL_MINUTES` | 15m |
| snapshot staleness — a claim is not judged against older board state | `governance/dispatch/snapshot.DEFAULT_STALENESS_MINUTES` | 15m |
| claim reap threshold — a gone holder's claim may be released | `governance/dispatch/cli` reap default | 45m |
| claim lease — how long a claim is held | `governance/dispatch.claims.DEFAULT_TTL_HOURS`, `--ttl-hours` | 24h |
| directive lifetime — a directive the sister never drains | not declared | 48h |

The relationships had to be derived by reading three modules:

* the **session TTL must exceed the rung heartbeat**, or a live lane is judged
  dead between two beats;
* the **claim TTL must exceed the session TTL**, or a claim is reaped while its
  lane is still legitimately running.

## The policy

`governance/policy/lease.py` declares every value once — in its natural unit,
with its owning module and the reason it has that value — and states the
relationships as **machine-checkable invariants** rather than prose:

| Invariant | Relation | Why |
|-----------|----------|-----|
| `session-ttl-exceeds-rung-heartbeat` | `session_ttl > rung_heartbeat` | a live lane must not be judged dead between two rung beats |
| `session-beat-fits-session-ttl` | `session_heartbeat < session_ttl` | a beat interval longer than the TTL would orphan a running session |
| `claim-ttl-exceeds-session-ttl` | `claim_ttl > session_ttl` | a claim must not be reaped while its lane is still legitimately running |
| `claim-reap-exceeds-session-ttl` | `claim_reap > session_ttl` | the reap threshold must not release a claim whose lane is still beating |
| `directive-lifetime-covers-claim-ttl` | `directive_lifetime > claim_ttl` | the authorisation must outlive the lease it authorises |

`python3 governance/policy/lease.py dump` prints the table live; `json` prints it
machine-readably.

## The modules read the policy

No consumer declares its own value any more — each reads the policy:

| Module | Constant | Reads |
|--------|----------|-------|
| `fleet/channel.py` | `STALE_HEARTBEAT_SECONDS` | `lease.RUNG_HEARTBEAT_SECONDS` |
| `fleet/channel.py` | `DIRECTIVE_LIFETIME_SECONDS` | `lease.DIRECTIVE_LIFETIME_SECONDS` |
| `governance/reconcile/heartbeat.py` | `DEFAULT_TTL_MINUTES` | `lease.SESSION_TTL_MINUTES` |
| `governance/reconcile/heartbeat.py` | `DEFAULT_BEAT_SECONDS` | `lease.SESSION_HEARTBEAT_SECONDS` |
| `governance/dispatch/claims.py` | `DEFAULT_TTL_HOURS` | `lease.CLAIM_TTL_HOURS` |
| `governance/dispatch/claims.py` | `DEFAULT_REAP_MINUTES` | `lease.CLAIM_REAP_MINUTES` |
| `governance/dispatch/model.py` | `ClaimEvent.ttl_hours` default | `lease.CLAIM_TTL_HOURS` |
| `governance/dispatch/snapshot.py` | `DEFAULT_STALENESS_MINUTES` | `lease.SNAPSHOT_STALENESS_MINUTES` |
| `governance/dispatch/cli.py` | reap `--older-than-minutes` default | `claims.DEFAULT_REAP_MINUTES` |

The directive lifetime is used where it bites: `fleet/channel.py status` reports
pending inbox directives older than the lifetime as *abandoned, not queued*.

## The gate

`scripts/check-lease-policy.sh` (`make lease-policy`, wired into `make verify`
and `make lint`) runs four checks and fails on any of them:

1. **values + invariants** — `lease.py check`: every invariant holds.
2. **consumer contract** — `lease.py scan`: every consumer reads the policy, and
   no tracked module under `fleet/` or `governance/` restates a policy value as
   its own numeric literal (name-based scan plus targeted literal guards for
   values that could be inlined without naming the constant).
3. **self-control** — `lease.py self-control`: every invariant has a mutation
   that must be refused *naming that invariant*, and a synthetic hard-code must
   be caught by the scan. A policy whose invariants cannot break is a formality
   (GR-12 / AO-GR-19).
4. **the requested mutation proof** — `check --mutate
   session-ttl-below-rung-heartbeat` must exit non-zero and name
   `session-ttl-exceeds-rung-heartbeat`.

Because (3) and (4) provoke the failures the gate exists to catch — in memory,
without touching the working tree — the gate cannot pass vacuously.

## Pointers

* `governance/policy/lease.py` — the policy module.
* `governance/policy/README.md` — the module's own contract.
* `scripts/check-lease-policy.sh` — the gate of record for the policy.
* `docs/EXECUTION-PLAN.md` — the dispatch contract the claim/reap TTLs serve.
