# governance/policy — declared policy: fleet leases + the policy registry

Two modules declare policy in this directory:

* **`registry.py`** — the **policy registry** (#1763): one row schema over
  every policy domain in this repository, and a **one-file-per-domain**
  registration contract. Read [The policy registry](#the-policy-registry-registrypy-1763)
  below before adding a domain.
* **`lease.py`** — the **single source of truth for every fleet lease and TTL**
  (issue #322).

The rest of this file documents `lease.py` first, then the registry.

## `lease.py` — the fleet's declared leases and TTLs

Before `lease.py`, four modules each declared their own timing:

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

---

## The policy registry (`registry.py`, #1763)

Every policy domain this repository *enforces* used to declare itself in its own
shape, in its own file, with no single read path — the GDC tag rules in
`governance/conformance/policy.yaml`, the isolation leases in
`governance/policy/lease.py`, Cloudflare/GCS/Git rules scattered across `infra/`
and `docs/` (RCA: `docs/rca/2026-09-21-policy-centralization-gap-review.md`).

`registry.py` is the one read path. It is the **structural sibling** of
`portal/server/settings.py` (#1756), not an extension of it: settings aggregates
*descriptive/observed* config (`editable: false`), whereas policy is
*prescriptive/enforced* state, so a policy row carries an **enforcement point**
and a **control-plane visibility** flag instead of a value.

### The one-row schema

```python
from governance.policy.registry import PolicyRegistry

for row in PolicyRegistry(repo_root).rows():
    print(row.domain, row.enforcement_point, row.control_plane_visible)
```

`PolicyRow` is a frozen dataclass with **exactly four fields**:

| field | type | meaning |
|---|---|---|
| `domain` | `str` | the domain name — the declaration file's **filename stem** |
| `source_file` | `str` | repo-relative path of the declared source of truth |
| `enforcement_point` | `str` | where the policy actually bites (a gate, a dispatch-time check, an apply pipeline) |
| `control_plane_visible` | `bool` | true only when the source exists and the domain is declared visible |

`rows()` is the verb; `aggregate()` is an alias with the same meaning (the name
`settings.py` uses). Rows are read **fresh on every call — nothing is cached**,
and discovery is `sorted()`, so the row order is deterministic.

### One file per domain

A domain is registered by adding exactly ONE file:

```text
governance/policy/domains/<domain>.yaml
```

The **filename stem is the domain name** (so renaming the file renames the
domain). No code change, no import, no registration table — which is what makes
sibling lanes file-disjoint: each adds one file and touches nothing else.

### The exact YAML keys

A flat mapping of exactly these four keys:

```yaml
domain: gdc
source_file: governance/conformance/policy.yaml
enforcement_point: "governance/conformance gate at issue-file time"
control_plane_visible: true
```

* `domain` — optional, decorative; it must match the filename stem if present.
* `source_file` — **required**, repo-relative. A directory is accepted: the
  check is *exists*, not *is a file*.
* `enforcement_point` — the place the policy bites; free text.
* `control_plane_visible` — read fail-closed: only `true` (or the strings
  `true`/`on`/`yes`, case-insensitive) is visible; anything else, including an
  absent key, is `false`.

See `domains/gdc.yaml` and `domains/isolation.yaml` for the two domains #1763
delivers, each with the reasoning in its own comments.

### The honesty rule (non-negotiable)

A domain is **never silently dropped**. A declaration the registry cannot stand
behind is still emitted — as a row with `control_plane_visible: false` and an
`enforcement_point` that **names the reason**:

* the declared `source_file` does not exist under the repo root →
  `not reporting: <path> absent`;
* the file is undecodable, or declares no `source_file`;
* the `domain:` key disagrees with the filename stem.

A caller iterating `rows()` therefore sees every domain, and every domain the
registry refuses to stand behind says so — there is no second channel to check.

### How to add a domain

1. Write `governance/policy/domains/<domain>.yaml` with the four keys above.
2. Point `source_file` at the file (or directory) that really is that policy's
   source of truth — do not invent one.
3. State the `enforcement_point` in your own words; say where it bites.
4. Run `python3 -m pytest governance/policy/tests -q` — the extension contract
   test (`test_one_new_file_registers_a_domain_with_no_code_change`) proves a
   new file appears as a new row with no code change.

The schema id is `ao.policy-registry/v1`.
