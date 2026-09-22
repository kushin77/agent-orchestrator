# Codebase hygiene gap review — headers, wrappers, env-vars, templates

**Date:** 2026-09-21
**Epic:** [kushin77/agent-orchestrator#1908](https://github.com/kushin77/agent-orchestrator/issues/1908)

Owner ask: ensure code is properly headered and tagged, wrapped/organized by
class/system/app, has a consistent template, and consistent environment-variable
patterns across the whole environment. "Dirty files" = files not yet conformant
to this repo's existing header/hygiene standard (`scripts/check-code-headers.sh`
+ `scripts/code-headers-baseline.tsv`) — no second standard invented.

Read-only investigation lane; no fixes implemented here.

## Measurement 1 — headers/tagging

Gate of record: `bash scripts/check-code-headers.sh`

```
code-headers: 1228 in scope — 940 conformant, 270 recorded, 18 drifted, 0 stale row(s)
code-headers: OK — every in-scope file carries a valid block or is recorded debt
```

The debt ledger (`scripts/code-headers-baseline.tsv`) has 288 non-comment
rows: 270 still-excused recorded debt + 18 drifted (edited since recorded,
content key voided — advisory at tier 0, refused once tier moves past 0).
270 + 18 = 288, consistent with the gate.

| Directory | Ledger rows | of which drifted |
|---|---|---|
| integrations | 174 | 3 |
| infra | 50 | 6 |
| fleet | 43 | 5 |
| e2e | 16 | 1 |
| .github | 4 | 3 |
| contrib | 1 | 0 |
| **Total** | **288** | **18** |

`portal/`, `gateway/`, `governance/`, `scripts/`, `control-plane/`,
`identity/` carry **zero** ledger rows — fully conformant on this measure.

## Measurement 2 — wrapper/organization (manifest convention)

No repo-wide `module.json`-per-directory or `SOLUTION-CLASSES.md` convention
exists. `module.json` is reserved for `cmr.module/v1` peer-repo admission
(root + `gateway/catalog/modules/*`), governed by `docs/MODULE-ADMISSION.md`.
The actual in-repo convention for an owned internal directory is a
`README.md`.

Measured across `governance/`, `gateway/`, `integrations/` (46 top-level
dirs): 39 have a README, 7 do not.

Unmanifested: `governance/controls`, `governance/onboarding`,
`governance/platform`, `governance/vocabulary`, `governance/waves`,
`gateway/sync`, `integrations/_seam` (underscore-prefixed — likely
deliberate-private, flagged for confirmation rather than counted as debt).

Also noted: `governance/board_selfheal.py` is a headered file with no owning
subdirectory — a single instance of the loose top-level pattern this
measurement checked for.

## Measurement 3 — environment-variable pattern

158 raw `os.environ`/`os.getenv` call sites across 71 files, outside the
declared flags/config modules (`gateway/chat/flags.py`,
`gateway/providers/flags.py`, `portal/server/config_flags.py`,
`integrations/erp/webhooks/flags.py`, `control-plane/cockpit/cockpit/flags.py`,
`integrations/paperclip/adapters/sync/flags.py`) and tests.

By directory: fleet 50, governance 28, infra 18, scripts 14, telemetry 13,
portal 11, e2e 7, gateway 6, control-plane 5, integrations 3, identity 1,
guardrails 1, engine 1.

Secret-shaped (name matches API_KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL,
resolved through the named constant): 3 hits — `gateway/providers/nous.py:113`
(`NOUS_API_KEY_ENV`), `fleet/lease.py:193` (`KEYDB_PASSWORD_ENV`),
`infra/fleet/secrets_contract.py:73` (the secrets contract's own injection
seam). Cross-checked against `infra/env/registry.yaml`: both `NOUS_API_KEY`
and `KEYDB_PASSWORD` are already declared there. **No undeclared
secret-shaped env reads — no P0.** The remaining 155 are plain config
bypassing the flags/config aggregators — organizational debt (P2).

Caveat: this measurement resolves the constant name at each call site; a
read through a fully opaque, non-descriptive constant would not be caught.
None found in this pass.

## Measurement 4 — templates

No single "new module" scaffold exists for governance/gateway/integrations
directories. Narrow, purpose-built templates do: `control-plane/fleet-template/`,
`control-plane/sdk/template/`, `docs/decision-records/template.md` (ADR),
`governance/lessons/rca-template.md`. Spot-check of 3 recently-touched
directories (`governance/futureproof`, `governance/lane-record`,
`gateway/sme-routing`) shows a consistent de facto shape — README.md + an
entry module + tests/ — followed by convention only, not written down or
gate-checkable.

## Gaps by category

| Category | Count | Severity | Smallest fix |
|---|---|---|---|
| Header debt, integrations/ | 174 rows (3 drifted) | P2 | header sprint |
| Header debt, infra/ | 50 rows (6 drifted) | P2 | header sprint |
| Header debt, fleet/ | 43 rows (5 drifted) | P2 | header sprint |
| Header debt, e2e/.github/contrib | 21 rows (4 drifted) | P2 | header sprint |
| Unmanifested top-level dirs | 6 (+1 to confirm) | P2 | add README per dir |
| Undeclared plain-config env reads | 155 | P2 | route through nearest flags/config module |
| Undeclared secret-shaped env reads | 0 | — | none — verified declared |
| No written module-scaffold doc | 1 (repo-wide) | P2 | write down the README+entry+tests shape |

## Children filed (7, under the 8-child cap)

- #1910 — header debt, integrations/
- #1911 — header debt, infra/
- #1912 — header debt, fleet/
- #1913 — header debt, e2e/.github/contrib
- #1914 — README manifests, 6 unmanifested dirs
- #1915 — route 155 plain-config env reads through flags/config modules
- #1916 — write down the de facto module scaffold
