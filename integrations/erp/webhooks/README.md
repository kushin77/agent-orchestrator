# `integrations/erp/webhooks/` — CRM→ERPNext conversion-event bridge (#671)

Parent: EPIC #665 (ERPNext & DeepSeek FinOps). Blocked-by (cleared): #645 (ERP
module epic, closed). PF-2 (#667, `docs/erp-finops/token-baseline.md`)
inventoried the CRM conversion hooks this lane is scoped against.

## What this lane does

Bridges a CRM **conversion event** — a lead converting or an opportunity being
won — directly to a balanced general-ledger posting
(`integrations.erp.tx.ledger`), so the ledger is written by the event, not by
a human transcribing it later.

```
raw delivery -> auth.verify -> schema.parse -> idempotency check -> GL derive+verify -> posted
                    |               |                                     |
                 auth-failed   schema-violation                   unbalanced-posting / missing-policy
                    \_______________\____________________________________/
                                     v
                              quarantine.QuarantineStore
```

Every arrow that is not "posted" ends in the quarantine store
(`quarantine.py`), never a half-applied ledger write — see `bridge.py`'s
module docstring for the exact sequence and why it cannot half-post.

## Files

| File | Role |
|---|---|
| `model.py` | event envelope (`ConversionEvent`), closed refusal vocabulary, `Refused` |
| `schema.py` | hand-written validator mirroring `schema/conversion-event.schema.json` |
| `auth.py` | HMAC-SHA256 signature verification, fail-closed |
| `idempotency.py` | in-memory idempotency store keyed by `tenant:eventId` |
| `retry.py` | exponential backoff for transient (non-`Refused`) failures only |
| `quarantine.py` | append-only record of every refused delivery |
| `bridge.py` | `ConversionBridge` — wires the above into one `handle()` seam |
| `flags.py` | this lane's own feature flag (`erp-webhooks-bridge`, default off) |
| `hooks.py` | PF-2 hook-inventory disposition (bridged vs. explicitly out-of-scope) |

## Hook inventory disposition (acceptance criterion)

PF-2 inventoried five in-repo hooks (tenant lifecycle, plan→entitlement→RBAC,
subscription status, trial pause, provisioning/activation audit) — all
identity/portal signup machinery, none a CRM conversion — and one
CANNOT-ASSESS item (no external CRM integration exists in-tree). `hooks.py`
records an explicit **out-of-scope** disposition, with reason, for every one
of those six PF-2 rows; none of them is bridged, because none represents a
sale converting to a ledger entry, and none is a file this lane is allowed to
touch (`integrations/erp/webhooks/**` only).

The two events this lane **does** bridge are the only in-tree CRM-family
conversion events that exist: `integrations/erp/crm/flows.py`'s
`convert_lead` (lead → customer) and `win_opportunity` (opportunity → won),
declared as `HOOK_LEAD_CONVERSION` / `HOOK_OPPORTUNITY_WIN` in `model.py`.
`tests/test_hooks_inventory.py` parses PF-2's own table out of
`docs/erp-finops/token-baseline.md` and fails if a hook it lists has no
disposition here.

## Fail-closed posture

* A malformed payload (`schema-violation`/`invalid-body`) is quarantined, never
  partially posted — `schema.parse` builds the full event or raises; there is
  no intermediate state.
* A missing/invalid signature (`auth-failed`) is checked **before** the
  payload is parsed at all.
* A derived posting that does not balance (`unbalanced-posting`) or a caller
  policy missing a required account role (`missing-policy`) is refused before
  the ledger is mutated — `bridge.py` builds the candidate ledger, calls
  `GeneralLedger.verify()`, and only commits (`self.ledger = candidate_ledger`)
  once verification returns no findings.
* A duplicate delivery (same `idempotencyKey`) returns the first recorded
  result verbatim and never re-derives or re-posts.
* The bridge's own flag (`erp-webhooks-bridge`, default off) refuses **every**
  delivery as quarantined while off — "the flag was off" is itself an audited
  outcome, not a silent no-op.

## No ERPNext code vendored

The event envelope (`schema/conversion-event.schema.json`) is an original,
generic webhook-delivery shape — comparable to common HMAC-signed webhook
conventions — not a pattern harvested from ERPNext. This lane therefore carries
no `catalog/provenance.json` (GR-10 harvest record): there is nothing
harvested to record. The GL posting itself reuses
`integrations.erp.tx.ledger`'s `Entry`/`GeneralLedger`/`PostingPolicy` (ERP-03,
#648), which already carries its own provenance.

## The flag

Ships **OFF**. `erp-webhooks-bridge` defaults off in `flags.py`, independent of
the module-wide `erp-module` flag — the epic's non-goals require every new
surface a child lane adds to ship flag-gated off on its own, not merely
inherit the parent module's gate. Promotion is a reviewed go-live, per the
module README's own "The flag" section.

## Verification

```bash
python3 -m pytest integrations/erp/webhooks -q
python3 -m pytest integrations/erp -q
make verify
```
