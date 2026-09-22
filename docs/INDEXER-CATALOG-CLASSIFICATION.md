# Indexer catalog classification — the stub cannot be mechanical

The corrected shape of the "seed the uncataloged repos" work: why the L0/L1 split
in #1529 is unreachable, what is measured, who writes the entries, and where the
work now lives.

Issue: [#1637](https://github.com/kushin77/agent-orchestrator/issues/1637) ·
Parent: [#1510](https://github.com/kushin77/agent-orchestrator/issues/1510) ·
EPIC: [#1666](https://github.com/kushin77/agent-orchestrator/issues/1666) ·
Measured: **2026-09-20** (inventory) and **2026-09-21** (hub gap-audit)

## Why the stub is the classification

`vendor/CMR/catalog/validate.py` — the validator the hub's own `verify` depends
on — requires `["schema","id","name","type","source","governance"]`, and `type`
is a **closed 11-value enum with no neutral or `unclassified` member**
(`app service frontend backend middleware integration feature infra workflow
template governance`). The validator additionally enforces `id == directory name`
and `id` must match `^[a-z0-9-]+(\.[a-z0-9-]+)*$`. There is no `role`/`classes`/
`onboarded` field at all.

So the minimal stub #1529 specified (`{"id","role","classes","onboarded","source"}`)
is refused with `'schema' is a required property`, and any *passing* entry must
already carry a real `type`, `class[]` and `governance` block. **The stub is the
classification**; there is no L0 mechanical pass to split off.

## The measurement (reproducible, all read-only)

`vendor/CMR/catalog/validate.py`, run in a full scratch copy of the hub tree —
never in the submodule:

```
0. BASELINE  untouched catalog                            rc=0   11 module(s) checked, 0 failed
1. POSITIVE  {schema,id,name,type,source,governance}      rc=0   OK   zz-ok (repo check skipped)
2. NEGATIVE  #1529's literal stub                         rc=1
   FAIL zz-lit: schema: 'schema' is a required property (at /)
3. NEGATIVE  a real uppercase repo name as the id         rc=1
   FAIL ERP-CRM: schema: 'ERP-CRM' does not match '^[a-z0-9-]+(\.[a-z0-9-]+)*$' (at id)
4. NEGATIVE  id != directory name                         rc=1
   FAIL zz-dir: id 'different-id' != directory name 'zz-dir'
5. NEGATIVE  shared_integrations                          rc=1
   FAIL shared_integrations: schema: 'shared_integrations' does not match '^[a-z0-9-]+(\.[a-z0-9-]+)*$' (at id)
6. NEGATIVE  'role' where 'type' belongs                  rc=1
7. NEGATIVE  type: "unclassified"                         rc=1
   FAIL zz-badtype: schema: 'unclassified' is not one of ['app','service','frontend','backend','middleware',
        'integration','feature','infra','workflow','template','governance'] (at type)
8. RESTORED  rc=0   12 module(s) checked, 0 failed        (11 at the pin + the passing positive control)
```

## Inventory, corrected

| | #1529 says | measured |
|---|---|---|
| repos in the account | 113 | **114** |
| cataloged | 11 | 11 at the pin `b6c49aa`; **12 on hub `main`** (adds `agent-orchestrator`, `pmo`, `monitoring-stack`, `twilio`; drops `hermes-agents`, `ollama`, `paperclip`) |
| gap | 99 | **102** at the pin; **96 real** by `source.repo` (gap-audit CMR#1023) |
| archived repos | "at least one" (negative control) | **0** |

No measured source for `type` exists: `catalog/repos/repo-inventory.json`
(CMR-110) carries `module_id` for **0 of 106** rows, `module_type` for **0 of
106**, and `custom_properties_available` false **106 of 106**. `owning_sme` is a
hard ADR-0039 requirement (resolves against `catalog/sme-registry.tsv`), so a
mechanical pass would fabricate both fields.

19 of the gap names contain uppercase (invalid `id` as-is); `Shared_Integrations`
cannot be an id at all (the pattern admits no `_`); `kushin77/CMR` itself is in
the gap.

## Jurisdiction

The entries are the **hub's** to write. `AGENTS.md` hard DON'Ts — "never edit
another repo's files — direction/needs go to that repo's board" — plus
`CMR#635` ("the repo files a catalog-request on the CMR board; CMR registers
`catalog/modules/<id>/module.json` (metadata only, NG4)") mean this repo files
the request and the classification decisions; the hub writes the `module.json`
files and runs `catalog/validate.py`.

## Resolution — the three deliverables

1. **Catalog-request** — `kushin77/CMR#1021` (open): 102 entries, the schema
   constraint, and the decision the hub owns.
2. **Classification pass** — split into EPIC
   [#1904](https://github.com/kushin77/agent-orchestrator/issues/1904)
   (96 per-repo classifications is too large for one lane; each entry needs a
   real `type`, `class[]`, `governance`, a lowercase `id == dirname`, and an
   `owning_sme`).
3. **Corrected acceptance** — `kushin77/CMR#1023` (merged 2026-09-21): the
   missing-count is measured by each entry's `source.repo`, not by a
   case-sensitive diff of directory names against repo names (the old diff reads
   104 and can never reach 0: `erp-crm` ≠ `ERP-CRM`); the archived-repo negative
   control is unsatisfiable (0 archived repos).

## Verify

```bash
# hub clone; never the pinned submodule
python3 catalog/validate.py && echo CATALOG-OK
```

The hub catalog validates green today: **12 module(s) checked, 0 failed** at hub
`main`; the future classification epic (#1904) must keep that baseline green and
report the missing-count by `source.repo`.
