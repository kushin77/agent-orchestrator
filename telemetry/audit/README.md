# telemetry/audit — the read-only audit read model

> Owner lane: **telemetry** (issue #347, M26). Parent: EPIC #338. Doctrine:
> [`AGENTS.md`](../../AGENTS.md), [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md), ADR-0013.
> Sibling: [`telemetry/ledger`](../ledger/README.md) (issue #31).

This tree is the **serving seam for the audit surface**: it turns the
tamper-evident audit trail into the read-only, filterable, deterministic read
model the shell's Audit view consumes (served through the adopted paperclip
surface, ADR-0013). It owns no storage, no key material and no chain crypto —
every record is loaded through the merged ledger's public API.

| File | What it is |
| --- | --- |
| [`read_model.py`](read_model.py) | `AuditReadModel`: `filter`, `stats`, `verify_chain`, `trusted_tail` |
| [`cli.py`](cli.py) | the `list` / `verify-chain` / `stats` commands (tri-state exits) |
| [`tests/`](tests/) | the offline suite (determinism, filters, tamper detection, read-only) |

## The read model

```python
from audit.read_model import open_read_model

model = open_read_model("/var/lib/audit")
rows = model.filter(actor="agent:worker-1", severity="critical",
                    since="2026-09-01T00:00:00Z")
verdict = model.verify_chain()          # OK / NOT-OK / CANNOT-ASSESS
```

* **`records(tenant=None)`** — every record in scope, sorted by
  `(tenantId, seq)`. Fail closed: the ledger raises on a malformed or
  chain-broken file rather than returning a partially-verified trail.
* **`filter(**criteria)`** — the closed filter vocabulary below; any other field
  is refused by name. The result is sorted by `(tenantId, seq)`, so two runs
  over one revision are byte-identical.
* **`stats()`** — record counts per tenant, first/last stamp, tail sequence and
  the derived severity / actor-kind / action histograms, built from sorted keys.
* **`verify_chain(expected=None)`** — an honest tri-state verdict over every
  tenant chain (see below).
* **`trusted_tail()`** — the verified `(seq, hash)` anchor per tenant, safe to
  feed back into `verify_chain(expected=...)`.

### Filters

| Field | Matches |
| --- | --- |
| `actor` | the canonical `kind:id` principal; a bare id matches the id part |
| `agent` | shorthand for `actor=agent:<id>` (the actor kind must be `agent`) |
| `action` | the exact action string, e.g. `model.call` |
| `severity` | the derived severity rung (below) |
| `since` / `until` | inclusive RFC 3339 bounds on `ts` (a bare date is midnight UTC) |
| `entity` | the audited object: exact `evidence`, or `resource` exactly or as a prefix |
| `tenant` | restrict the scan to one tenant chain |

### Severity is derived, not stored

The audit-event contract ([`audit_event.schema.json`](../ledger/audit_event.schema.json))
carries **no severity** field, so this read model *derives* one — deterministically
and totally — from the action's final dot-segment (`model.call` → `call`):

| Rung | Verbs |
| --- | --- |
| `critical` | `breach`, `block`, `deny`, `denied`, `kill`, `revoke` |
| `warning` | `delete`, `escalate`, `override`, `rechain`, `rotate` |
| `notice` | `approve`, `decision`, `grant`, `register`, `sync`, `topup`, `update` |
| `info` | `call`, `get`, `list`, `query`, `read` — and any unknown verb |

Because the rule is total and documented, the `severity` filter is reproducible
rather than guessed; an unrecognised verb is `info`, never a hidden failure.

## verify-chain semantics

`verify_chain()` walks each tenant chain through the ledger's own verifier and
aggregates honest tri-state verdicts. `NOT-OK` outranks `CANNOT-ASSESS`; the
combined `exit_code` is `OK=0`, `NOT-OK=1`, `CANNOT-ASSESS=2`.

| Tamper | Detected as | Why |
| --- | --- | --- |
| **modified** record | `NOT-OK` at the record — `hash mismatch ... (content was altered)` | each record's hash covers every other field |
| **reordered** records | `NOT-OK` at the record — `seq ... does not match expected` / `broken hash link` | position, `seq` and `prevHash` must agree |
| **removed** record | `NOT-OK` at the gap — `seq ... does not match expected` | `seq` is contiguous from 1 |
| **removed trailing** record | invisible to the chain alone — pass the `trusted_tail()` anchor as `expected` to make the tail mismatch a `NOT-OK` | an internally consistent, shorter file is still consistent |
| **unparseable** file | `CANNOT-ASSESS` (`exit 2`) — **never a pass** | no honest verdict can be formed |

An unverifiable record is a finding, not a skip: `CANNOT-ASSESS` fails the gate
and `is_pass` is true only for `OK`. An empty scope is `CANNOT-ASSESS` too — a
verdict over nothing is not a pass.

## CLI

```bash
python3 telemetry/audit/cli.py /var/lib/audit list --actor agent:worker-1 --json
python3 telemetry/audit/cli.py /var/lib/audit list --severity critical --markdown
python3 telemetry/audit/cli.py /var/lib/audit verify-chain
python3 telemetry/audit/cli.py /var/lib/audit verify-chain --tenant acme \
    --expected-seq 42 --expected-hash <sha256>
python3 telemetry/audit/cli.py /var/lib/audit stats --json
```

`list` also takes repeatable `--filter KEY=VALUE`; an unknown key is refused.
`verify-chain` exits `0` / `1` / `2` and prints each finding on stderr. The read
model never decrypts a payload, so the CLI needs no key material.

## Read-only, structurally

There is no append, update, delete or repair path here. `AuditReadModel` calls
only the ledger's read API (`records`, `tenant_ids`, `verify`, `tail_state`),
and [`tests/test_read_only.py`](tests/test_read_only.py) proves it by parsing
the module's source (not by grepping text) and by asserting the instance's
public name set is exactly the read surface — a future write method fails the
suite. Writers use the ledger directly; this seam only reads.

## Serving through paperclip

[`integrations/paperclip/mapping.py`](../../integrations/paperclip/mapping.py)
projects audit records onto the upstream `Activity` shape
(`activity_from_audit`, `map_audit_activities`), so the read model is servable
through the adopted surface:

```python
from integrations.paperclip import mapping
activities = mapping.map_audit_activities(model.filter(tenant="acme"))
```

## Gate

[`scripts/check-audit-read-model.sh`](../../scripts/check-audit-read-model.sh)
(also wired as `make audit-read-model`, and into `make verify`) asserts, offline:
determinism, `OK` on an intact chain, `NOT-OK` naming the record for a modified /
reordered / removed record, `NOT-OK` for a truncated trail against the anchor,
`CANNOT-ASSESS` for an unparseable chain, refusal of an unknown filter field and
severity, and the structural read-only guarantee. Every tamper is provoked on a
**temp copy** — the committed ledger is never mutated — and the gate finishes
with a self-mutating negative control: a checker must certify the intact ledger
and then refuse a tampered copy by name, or the gate reports FAIL.
