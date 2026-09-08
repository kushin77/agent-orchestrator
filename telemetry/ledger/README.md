# telemetry/ledger — tamper-evident per-tenant audit ledger

> Owner lane: **telemetry** (issue #31, work item 27, phase 5). Parent:
> EPIC-00 (issue #4). Doctrine: [`AGENTS.md`](../../AGENTS.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md) (AO-GR-17),
> [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).

This tree is the **evidence layer of the control plane**: a per-tenant,
append-only, tamper-evident audit ledger that records every agent action and
policy decision (one event per action, AO-GR-17). Each record is chained to
the previous one with a SHA-256 hash, sensitive payloads are encrypted at rest
(AES-256-GCM), and each tenant's chain is isolated from every other tenant. It
turns "who did what, under whose policy, at what cost" into a mechanically
verifiable, per-tenant record - the promise the SaaS sells trust on.

It builds on the shape of the agent-registry append-only event log
([`registry/events`](../../registry/events/README.md), issue #10) but owns its
own contract: hash chaining plus **encrypted payloads at rest** plus
**per-tenant chain isolation** plus a **tri-state verify API**.

## The audit-event contract

One JSON object per line in a per-tenant JSON Lines file. The authoritative
machine-readable shape is [`audit_event.schema.json`](audit_event.schema.json);
the closed core is `schemaVersion`, `seq`, `ts`, `tenantId`, `actor`, `action`,
`prevHash`, `hash`; the rest is optional per action.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schemaVersion` | int = 1 | ✅ | Record-contract version (hashed into every record). |
| `seq` | integer ≥ 1 | ✅ | Monotonic per-tenant sequence; 1, 2, 3, … no gaps. |
| `ts` | string (RFC 3339 UTC) | ✅ | When the action happened. |
| `tenantId` | string | ✅ | The tenant (agent org) whose chain this belongs to. |
| `actor` | `kind:id` string | ✅ | Principal that caused the action (`user:alice`, `agent:worker-1`, `system:policy`). |
| `impersonatedBy` | `kind:id` \| null | — | Impersonation stamp - the principal the actor acted on behalf of. |
| `action` | string | ✅ | What was done, e.g. `model.call`, `policy.decision`, `registry.register`. |
| `resource` | string \| null | — | What resource the action concerned. |
| `evidence` | string \| null | — | Pointer to external supporting evidence (e.g. a source-log hash). |
| `modelUsed` | string \| null | — | Model id when the action was a model call. |
| `costUsd` | number/string \| null | — | Cost when relevant. |
| `payloadEnc` | object \| null | — | AES-256-GCM envelope of the sensitive payload; **the clear payload is never stored**. |
| `prevHash` | string (sha256 hex) | ✅ | Hash of the previous record (fixed genesis hash `0…0` for `seq` 1). |
| `hash` | string (sha256 hex) | ✅ | sha256 over the record's canonical JSON, `hash` field excluded. |

Example record (fields elided):

```json
{"schemaVersion":1,"seq":1,"ts":"2026-09-08T12:00:00Z","tenantId":"acme",
 "actor":"agent:worker-1","impersonatedBy":null,"action":"model.call",
 "resource":"gateway/proxy","evidence":null,"modelUsed":"claude-3-5-sonnet",
 "costUsd":"0.0021","payloadEnc":{"v":1,"alg":"AES-256-GCM","keyId":"acme:k1",
 "nonce":"...","ct":"..."},"prevHash":"0000…0000","hash":"abcd…"}
```

## Guarantees (each backed by a negative test)

### 1. Append-only + hash-chained
Records are never rewritten, deleted or reordered; each new record chains to
the previous one. `verify` recomputes every sequence, tenant binding, link and
hash. **Tampering - editing any field, deleting a record, reordering records,
or truncating the tail (when the trusted tail is supplied) - is DETECTED**
(`NOT-OK`), never silently passed (`tests/test_chain_integrity.py`).

### 2. Encrypted payloads at rest
A sensitive `payload` is AES-256-GCM encrypted into `payloadEnc` before it
enters the chain; **the plaintext is never stored** (a negative test greps the
on-disk file and every stored record for the plaintext marker). Appending a
payload with no tenant key, or when the cipher library is absent, **fails
closed** - the append is refused and nothing is written. There is no plaintext
fallback path (`tests/test_encryption.py`).

### 3. Per-tenant chain isolation
Each tenant has a physically separate chain file; every record is bound to its
chain's tenant; the store only reads/appends the requested tenant's chain.
**A foreign-tenant record smuggled into a chain is detected** (`NOT-OK`), and
one tenant's key cannot decrypt another tenant's payloads
(`tests/test_tenant_isolation.py`). Authorization over *who may append to or
read a tenant's chain* is enforced by the consuming service's identity/RBAC
layer (issue #36); the ledger enforces the cryptographic and structural
isolation beneath it.

### 4. Honest tri-state verify
`verify` reports exactly one of `OK` / `NOT-OK` / `CANNOT-ASSESS` (issue #28
vocabulary, exit codes 0 / 1 / 2). A tamper is a definite `NOT-OK`; an
unparseable/unreadable file is `CANNOT-ASSESS` (no honest verdict is possible,
and it is never a pass); an unavailable tenant key makes payload *decryption*
`CANNOT-ASSESS` while the hash chain itself (which covers ciphertext only)
still verifies. **There is no silent pass.**

## The rechaining procedure (documented recovery)

Normal operation is strictly append-only. Rechaining is the **opt-in,
destructive** recovery path used only after an independent check has
established that the record *content* is authoritative. Legitimate uses:

1. **Restore from a trusted replica/backup** - the physical file was corrupted
   or truncated, but the restored content was verified against an independent
   source.
2. **Key rotation with re-encryption** - an operator replaced `payloadEnc`
   envelopes with a trusted admin step (envelopes are versioned + key-tagged,
   so the old key stays readable); the links must be rebuilt over the new
   ciphertext bytes.

Rechaining recomputes `seq` / `prevHash` / `hash` over the current content of
every record, preserving all other fields (including encrypted envelopes). It
**never runs without explicit `acknowledge=True`** (`RepairRefusedError`), and
the caller logs it. A verification must be run before and after. Rechaining
cannot make tampered content trustworthy - content is only ever re-linked, and
only because an operator decided it is authoritative.

```python
# After: independent verification that the on-disk content is authoritative...
ledger.rechain("acme", acknowledge=True)
assert ledger.verify("acme").status == "OK"   # exit 0
```

## Keys: injected, never stored

Keys are never in code, in the ledger, or in the repo. Sources, in resolution
order:

1. `DictKeystore` - in-process injection (tests, embedded control plane).
2. Environment: `AO_AUDIT_LEDGER_KEY_<TENANT>` (64 hex chars = 32 bytes),
   optional `AO_AUDIT_LEDGER_KEY_ID_<TENANT>`.
3. `FileKeystore` - a JSON file `{"<tenant>": {"key": "<64 hex>", "id": "…"}}`.

A tenant with no resolvable key cannot store a sensitive payload (fail
closed). See [`keystore.py`](keystore.py).

## Public API

```python
import sys; sys.path.append("telemetry")   # or PYTHONPATH=telemetry
from ledger import open_ledger, DictKeystore, KeyMaterial, verify_ledger
from ledger.schema import encode_actor

keystore = DictKeystore({"acme": KeyMaterial(key=b"\x01" * 32, key_id="acme:k1")})
ledger = open_ledger("/var/lib/control-plane/audit", keystore=keystore)

record = ledger.append(
    "acme",
    actor=encode_actor("agent", "worker-1"),
    action="model.call",
    resource="gateway/proxy",
    model_used="claude-3-5-sonnet",
    cost_usd="0.0021",
    payload={"prompt": "...", "apiKey": "..."},   # encrypted at rest
)
verdict = verify_ledger(ledger, "acme")            # OK / NOT-OK / CANNOT-ASSESS
tail = ledger.tail_state("acme")                   # trusted (seq, hash) anchor
records = ledger.export("acme")                    # per-tenant compliance export
```

## Intake from audit-event producers

The adapter in [`adapter.py`](adapter.py) ingests **registry/events-shaped**
records (the `registry` lifecycle log, issue #10) onto the audit ledger. It is
**read-only** over the source record (never mutates it, never imports the
registry package - it consumes the documented wire shape), maps the registry
event to a tenant-scoped, encrypted, hash-chained audit record, and documents
that mapping contract. See the module docstring and
`tests/test_adapter.py`. Other producers (gateway, engine, guardrails,
telemetry/metering lanes) append their own `action`s directly.

## CLI

Run from the repo root with `telemetry/` importable. Keys come from the
environment or `--keystore <file>`.

```bash
PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> append acme \
    --actor agent:worker-1 --action model.call --resource gateway/proxy \
    --payload-file ./payload.json            # payload encrypted at rest
PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> verify acme      # exit 0/1/2
PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> verify --all
PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> export acme      # compliance
PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> read acme 1      # decrypt
PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> state acme       # tail anchor
PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> rechain acme --ack  # opt-in repair
```

## Layout

| Path | Purpose |
|---|---|
| [`schema.py`](schema.py) | Record contract, canonical JSON, `build_record`/`validate_record`. |
| [`errors.py`](errors.py) | Typed exception hierarchy (fail-closed leaves). |
| [`crypto.py`](crypto.py) | AES-256-GCM envelope encryption; fail-closed cipher seam. |
| [`keystore.py`](keystore.py) | Injected per-tenant key resolution (env/file/dict). |
| [`store.py`](store.py) | `LedgerStore` / `LedgerVerdict`: per-tenant chains, verify, opt-in rechain. |
| [`verify.py`](verify.py) | Public tri-state verify API + payload-read access. |
| [`cli.py`](cli.py) | Command-line interface (append / verify / export / read / state / rechain). |
| [`adapter.py`](adapter.py) | Intake adapter for registry/events-shaped records (read-only). |
| [`audit_event.schema.json`](audit_event.schema.json) | JSON Schema mirror of the record contract. |
| [`tests/`](tests/) | pytest suite: integrity negatives, encryption, isolation, tri-state, CLI, adapter, schema. |

## Importing and running the tests

`telemetry/ledger` is a self-contained package, importable as `ledger` when
`telemetry/` is on `sys.path` (the tests arrange this in
`tests/conftest.py`):

```bash
# from the repo root
python3 -m pytest telemetry/ledger/tests -q -p no:cacheprovider
make -C <repo-root> verify   # repo gate must stay green
```

## Verification summary (issue #31)

- `python3 -m pytest telemetry/ledger/tests -q` → green: hash-chain integrity
  (edit / delete / reorder / truncate / cross-tenant insertion all detected),
  encrypted payloads at rest with a plaintext-never-stored negative test and
  fail-closed behavior on missing key/cipher, per-tenant isolation negatives,
  honest OK/NOT-OK/CANNOT-ASSESS verify + exit codes, CLI end-to-end, adapter
  intake, and JSON-Schema conformance.
- `make verify` → green.

## Provenance (cannibalized and adapted)

Read through the `.research/` read-only mirrors (GR-10), not copied verbatim.
Sources are indexed in [`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `capital-underwriting` `apps/server/src/lib/auditChain.ts` | Per-tenant hash chain: `prevHash` = previous `rowHash`; canonical hashing; first-broken-record reporting | `store.py` chain walk + `LedgerVerdict.broken_at` |
| `capital-underwriting` `apps/server/src/lib/secureAuditLog.ts` | Versioned AES-256-GCM envelope at rest; fail-closed when a configured key is missing; versioning over destructive re-encryption | `crypto.py` envelope + `keystore.py` fail-closed |
| `leaderboard` `scripts/audit/immutable-ledger.sh` + `verify-audit-chain.sh` | Immutable hash-chained audit trail with an explicit verify step | Chain verify + `tail_state` truncation anchor |
| `shared-services` `automation/contract_help/audit_hmac.py` | Deployment-injected key (env/secret); deterministic JSON canonicalization; `compare_digest` | `keystore.py` env/file sources + `crypto.py` canonical plaintext |
| `CMR` / merged `registry/events` (issue #10) | Append-only one-object-per-line JSON Lines log; header comments; malformed line = hard failure | Storage layout, header, fail-closed reads |
| `git-rca-workspace` `store/event_store.py` | Event-source record with payload + version/sequence | Record `seq`/`payload`/`payloadEnc` split |
| Fleet no-false-green doctrine (`AGENTS.md`) | A check that cannot fail is a formality | Honest tri-state verify; negative tests for every guarantee |
