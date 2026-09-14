# governance/policy — the fleet's declared policies

`lease.py` is the **single source of truth for every fleet lease and TTL**
(issue #322). Before it, four modules each declared their own timing:

| Timing | Was declared in | Value |
|--------|-----------------|-------|
| rung heartbeat | `fleet/channel.STALE_HEARTBEAT_SECONDS` | 120s |
| session heartbeat | `governance/reconcile/heartbeat.DEFAULT_BEAT_SECONDS` | 60s |
| session / reconcile TTL | `governance/reconcile/heartbeat.DEFAULT_TTL_MINUTES` | 15m |
| snapshot staleness | `governance/dispatch/snapshot.DEFAULT_STALENESS_MINUTES` | 15m |
| claim reap threshold | `governance/dispatch/cli` reap default | 45m |
| claim lease | `governance/dispatch.DEFAULT_TTL_HOURS` / `--ttl-hours` | 24h |
| directive lifetime | not declared | 48h |

The relationships between those numbers are load-bearing — the session TTL must
exceed the rung heartbeat or a live lane is judged dead between beats; the claim
TTL must exceed the session TTL or a claim is reaped while its lane is still
running. `lease.py` states them as **machine-checkable invariants**, not prose.

## The module

* `LEASES` — every declared value with its unit, owning module and reason.
* `INVARIANTS` — the ordering constraints, each a named relation between two
  values (`session-ttl-exceeds-rung-heartbeat`, `claim-ttl-exceeds-session-ttl`,
  `claim-reap-exceeds-session-ttl`, `session-beat-fits-session-ttl`,
  `directive-lifetime-covers-claim-ttl`).
* `CONSUMERS` / `INDIRECT_READS` / `LITERAL_GUARDS` / `OWNED_NAMES` — the
  consumer contract the gate enforces: a consumer must read the policy and must
  not restate a value as its own numeric constant.
* `MUTATIONS` — one mutation per invariant, each of which must be refused.

## Commands

```bash
python3 governance/policy/lease.py check      # values + invariants
python3 governance/policy/lease.py scan       # consumers read it; no hard-codes
python3 governance/policy/lease.py self-control
python3 governance/policy/lease.py dump | json
python3 governance/policy/lease.py check --mutate session-ttl-below-rung-heartbeat
```

The gate of record is `scripts/check-lease-policy.sh` (`make lease-policy`, part
of `make verify` and `make lint`). It fails when a module hard-codes a value the
policy owns, and when an ordering invariant is broken — proven by the mutations
above, so it cannot pass vacuously (GR-12).

## Consuming the policy

```python
from governance.policy import lease

STALE_HEARTBEAT_SECONDS = lease.RUNG_HEARTBEAT_SECONDS
```

Never restate the number. The architecture note is `docs/LEASE-POLICY.md`.
