# Spike #47 — Canonical-copy ownership (MODEL/SME/SOLUTION-CLASSES trio + agent-identity standards)

> **Type:** research spike (read-only decision support — no product code).
> **Status:** complete.
> **Date:** 2026-09-08.
> **Issue:** [kushin77/agent-orchestrator#47](https://github.com/kushin77/agent-orchestrator/issues/47) (EPIC-00, phase 8).
> **Lane:** issue-47-spike-canonical-copy — owns `docs/spikes/47-canonical-copy-ownership.md`, `docs/decision-records/ADR-0010-canonical-copy-ownership.md`, the ADR-0010 index row, and `governance/dupcheck/`.
> **Method:** read-only byte/content comparison (`md5sum`, `wc -l`, `diff -r`) of the `.research` source clones and the pinned `vendor/CMR` submodule, plus the distilled findings in `docs/CANNIBALIZATION.md` §3 and the harvest reports under `.research/reports/`. No source repo was modified.

## TL;DR

The fleet-standard trio (`MODEL-PROFILES` / `SME-PROFILES` / `SOLUTION-CLASSES`)
exists as **near-identical, repo-adapted copies in three repos** — CMR, shared-
frontend, code-indexing — and **no two copies are byte-identical**; all have
drifted. The decision ([ADR-0010](../decision-records/ADR-0010-canonical-copy-ownership.md))
names **CMR as the single canonical home for the trio** (ratifying the
`CANNIBALIZATION.md` §3.1 working default, backed by code-indexing's
provenance analysis that calls CMR the "outer authority"), keeps **shared-
governance `GLOBAL_STANDARDS/`** as the canonical home for the agent-identity
standards (single home today), confirms the **`dprs` ≡ `git-rca-workspace`
byte-identical duplicate** (930 files each, zero diff), and ships an automated
duplicate-detection helper (`governance/dupcheck/check-duplicates.sh`) so the
product never forks a canonical doc it consumes.

## 1. Scope and method

Read-only inspection of the local source clones:

| Source (clone HEAD, 2026-09-08) | Paths compared |
|---|---|
| `kushin77/CMR` (`ccb03cc`) + pinned `vendor/CMR` submodule (`b6c49aa`) | `docs/MODEL-PROFILES.md`, `docs/SME-PROFILES.md`, `docs/SOLUTION-CLASSES.md` |
| `kushin77/shared-frontend` (`b646eff`) | `docs/` same trio |
| `kushin77/code-indexing` (`3b95027`) | `docs/` same trio |
| `kushin77/shared-governance` (`52aeec9`) | `GLOBAL_STANDARDS/agent-identity.md` + `GLOBAL_STANDARDS/schemas/` identity schemas |
| `kushin77/dprs` (`b4937f7`) vs `kushin77/git-rca-workspace` (`6735747`) | whole trees |

Comparison commands: `md5sum` (byte identity), `wc -l` (size), `diff -r
--exclude=.git` (tree identity). All findings below are byte-verified, not
assumed.

## 2. The trio — copy inventory

| Doc | CMR `docs/` | shared-frontend `docs/` | code-indexing `docs/` |
|---|---|---|---|
| `MODEL-PROFILES.md` | 183 lines · md5 `16677c1e165c667560e091f402e5d935` (snapshot); `7b1513fdfa64b39f8f10b51cbcd0f1ce` at vendor pin `b6c49aa` | 29 lines · md5 `15a2b4963e256f07296f516dd5674345` | 108 lines · md5 `9c08bb23f819e779f9a781a5b3666bb5` |
| `SME-PROFILES.md` | 165 lines · md5 `92e5313234e786029e7b98d0c8ad6269` | 57 lines · md5 `d4b33ea2c2fc3259c8ae77dafd585a75` | 129 lines · md5 `1c983704ec008754344ef9e6015fcf34` |
| `SOLUTION-CLASSES.md` | 77 lines · md5 `30fac2cec85d75a8dc05639e0fff4ab4` | 39 lines · md5 `64e3a60ae83940fab46fefc03536a01f` | 86 lines · md5 `a8f4b5d2275dd6b1a397a4280c12eb9d` |

**Finding.** All nine files carry the same ladder families (model tiers, SME
persona names, quality rungs) but are **independent, repo-specific
derivations** — none is a byte-identical copy of another. The vendor/CMR pin
`b6c49aa` is newer than the CMR snapshot `ccb03cc` for `MODEL-PROFILES.md`
(the pinned copy adds a "Divergence note (deliberate)" explaining how CMR's L1
split differs from the global ladder) — evidence the trio is still actively
edited in CMR.

## 3. Authority signals (who already calls whom canonical)

| Signal | Verdict |
|---|---|
| `code-indexing` provenance notes (2026-09-06, GR-10) | CMR = **"outer authority"** for `MODEL-PROFILES` + `SOLUTION-CLASSES`; shared-frontend = **"origin"**; persona spec = shared-frontend `SME-PROFILES.md` "(mirrored at CMR)" |
| `docs/CANNIBALIZATION.md` §3.1 | Trio canonical copy **TO BE DECIDED**; "CMR is the natural hub … treat CMR as the working default" |
| CMR harvest report | `MODEL-PROFILES.md` = "core doc for the SaaS model chooser"; `SME-PROFILES.md` = "authoritative SME persona spec" |
| shared-frontend harvest report | Trio labelled the **"fleet-standard trio"**, all READY-TO-REUSE; `SME-PROFILES.md` = canonical persona template |
| product issue #11 | Harvested persona spec from `shared-frontend/docs/SME-PROFILES.md` as canonical template |

The trio is a **co-evolving unit** (each file cross-references the other two and
the shared `EXECUTION-PLAN.md`), so the decision keeps it in one home rather
than splitting `SME-PROFILES` from its siblings on the shared-frontend-origin
evidence. CMR wins on hub authority, maturity/citation, the third-party
"outer authority" label on two of the three docs, and `CANNIBALIZATION.md`'s
standing CMR default.

## 4. Agent-identity standards — single home confirmed

`kushin77/shared-governance` `GLOBAL_STANDARDS/agent-identity.md` + identity
schema set (`agent-identity-jwt.schema.json`, `agent-action.schema.json`,
`agent-oidc-config.schema.json`, `agent-task.schema.json`) is the **only home**
of the agent-identity standard; no byte-identical or competing copy was found
in the other clones. The product's reserved pillar ADRs (ADR-0002 agent-
registry, ADR-0007 identity/RBAC, and the observability/guardrails set) already
adopt from this home. The decision ratifies it.

## 5. `dprs` ≡ `git-rca-workspace` — byte-identical (re-verified)

`kushin77/dprs` is a **byte-identical copy** of `kushin77/git-rca-workspace`:

```text
file count (excluding .git):   dprs 930   git-rca-workspace 930
diff -r --exclude=.git:         0 lines of difference
README.md md5 (both):           158e52b38ed8521427f8779918482fe9
```

This reproduces the `CANNIBALIZATION.md` §3.2 finding (README md5 identical to
the documented `158e52b38ed8521427f8779918482fe9`). Canonical home:
`kushin77/git-rca-workspace`; treat `dprs` as an alias and never double-count
its assets in the product BOM.

## 6. Decision — canonical homes (ADR-0010, accepted)

| Doc set | Canonical home | Other copies |
|---|---|---|
| `MODEL-PROFILES.md` · `SME-PROFILES.md` · `SOLUTION-CLASSES.md` | `kushin77/CMR` `docs/` | shared-frontend = origin-lineage mirror; code-indexing = consumer — both reference-with-provenance, never fork |
| `agent-identity.md` + identity schema set | `kushin77/shared-governance` `GLOBAL_STANDARDS/` | adopted by reference in reserved pillar ADRs |
| dprs tree (duplicate) | `kushin77/git-rca-workspace` | `dprs` = byte-identical alias, never double-counted |

## 7. Automated duplicate detection

`governance/dupcheck/check-duplicates.sh` makes the "never fork" half of the
decision mechanical. Demonstration output (worktree, 2026-09-08):

```text
$ governance/dupcheck/check-duplicates.sh demo-cases
dupcheck scan: PASS - no forked copies of protected canonical docs (8 names checked)
dupcheck compare: IDENTICAL (/home/akushnir/agent-orchestrator/.research/fleet/dprs = /home/akushnir/agent-orchestrator/.research/fleet/git-rca-workspace)
```

`scan` fails (exit 1) the moment a protected canonical doc name appears in-repo
outside `vendor/` and `.research/`; `compare` re-verifies the dprs ≡
git-rca-workspace byte-identity on demand. See `governance/dupcheck/README.md`.

## 8. Product consumption and pins

- **Trio:** consumed from CMR via the pinned `vendor/CMR` submodule gitlink
  `b6c49aa03992dba9fe4b87b46104b8fc2f69f224` (read-only reference; the pin is
  the version lock).
- **Agent-identity standards:** consumed by reference from `kushin77/shared-
  governance` `GLOBAL_STANDARDS/**`; analysis reference commit `52aeec9`
  (2026-09-08).
- **No forked copies in-tree:** verified — zero protected-doc collisions
  outside `vendor/`.

## 9. Consequences and follow-ups

- shared-frontend / code-indexing convert their trio copies to
  reference-with-provenance pointers (their own repos).
- `CANNIBALIZATION.md` §3.1 updates to "resolved by ADR-0010" on its next
  owning-lane touch.
- Wiring `dupcheck scan` into `make verify` lands under the `scripts/` lane.
- Phase-8 ADR-0009 records the shared-governance reference pin in a consumption
  manifest.

*End of report. Raw evidence: `$TMPDIR/ao47-evidence/*` (byte comparisons,
issue body, gate output).*
