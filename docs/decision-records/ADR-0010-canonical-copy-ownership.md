---
id: ADR-0010
status: accepted
date: 2026-09-08
deciders: [owner]
req: []
supersedes: []
---

# ADR-0010: Canonical-copy ownership (MODEL/SME/SOLUTION-CLASSES trio + agent-identity standards)

## Status

`accepted` — ratified by issue #47 (decision spike, EPIC-00 phase 8). Resolves
the "canonical copy TO BE DECIDED" flag in
[`../CANNIBALIZATION.md`](../CANNIBALIZATION.md) §3.1 before the product
hard-references the fleet-standard docs.

## Context

The fleet-standard "trio" — `MODEL-PROFILES.md`, `SME-PROFILES.md`,
`SOLUTION-CLASSES.md` (model tiering / SME personas / solution-quality ladders)
— exists as **near-identical, adapted copies in at least three repos**. A
read-only byte analysis of the `.research` clones (2026-09-08) shows **none of
the copies are byte-identical**: each is a derivation with repo-specific
examples that has drifted in size and content.

| Doc | CMR (`docs/`) | shared-frontend (`docs/`) | code-indexing (`docs/`) |
|---|---|---|---|
| `MODEL-PROFILES.md` | 183 lines · md5 `16677c1e…` (snapshot `ccb03cc`); vendor mirror md5 `7b1513fd…` at pin `b6c49aa` (newer, adds a deliberate CMR/global divergence note) | 29 lines · md5 `15a2b496…` | 108 lines · md5 `9c08bb23…` |
| `SME-PROFILES.md` | 165 lines · md5 `92e53132…` | 57 lines · md5 `d4b33ea2…` | 129 lines · md5 `1c983704…` |
| `SOLUTION-CLASSES.md` | 77 lines · md5 `30fac2ce…` | 39 lines · md5 `64e3a60a…` | 86 lines · md5 `a8f4b5d2…` |

Authority signals gathered during the spike:

- `docs/CANNIBALIZATION.md` §3.1 flags the trio as canonical copy **to be
  decided** and recommends the hub: *"CMR is the natural hub … Until decided,
  treat CMR as the working default."*
- `code-indexing` — the one repo that performed a provenance adoption analysis
  (GR-10 lines dated 2026-09-06) — calls CMR the **"outer authority"** for
  `MODEL-PROFILES` and `SOLUTION-CLASSES` and shared-frontend the **"origin"**;
  for `SME-PROFILES` it records *"the org persona spec is
  `shared-frontend/docs/SME-PROFILES.md` (mirrored at CMR)"*.
- The CMR copies are the most mature and most-cited (CMR-harvest marks
  `MODEL-PROFILES.md` "core doc for the SaaS model chooser" and `SME-PROFILES.md`
  "authoritative SME persona spec").
- shared-frontend is the **origin lineage**: its copies are labelled the
  "fleet-standard trio", its `SME-PROFILES.md` is cited as the canonical persona
  template by issue #11 and the shared-frontend harvest, and the fleet's global
  operating instructions reference shared-frontend's `docs/` for these ladders.

The agent-identity standards are a different shape: `agent-identity.md` plus
its schema set (`agent-identity-jwt.schema.json`, `agent-action.schema.json`,
`agent-oidc-config.schema.json`, `agent-task.schema.json`) have a **single
home today** — `kushin77/shared-governance` `GLOBAL_STANDARDS/` — and the
product's reserved pillar ADRs (ADR-0002 agent-registry, ADR-0007 identity/RBAC)
already name that home as the adoption source. No canonical-copy conflict exists
there; the decision records it so later pillars reference rather than fork.

A separate duplicate finding is already byte-confirmed in
`docs/CANNIBALIZATION.md` §3.2 and re-verified for this ADR:
`kushin77/dprs` is a **byte-identical copy** of `kushin77/git-rca-workspace`
(930 files each; `diff -r --exclude=.git` = 0 lines; `README.md` md5
`158e52b38ed8521427f8779918482fe9` in both). The harvest doctrine — count the
assets once, from `git-rca-workspace` — needs a decision-record home.

## Decision

1. **One canonical home per doc; every other copy references or mirrors with
   provenance, never forks.**
   - The fleet-standard trio — `MODEL-PROFILES.md`, `SME-PROFILES.md`,
     `SOLUTION-CLASSES.md` — has its **single canonical home in
     `kushin77/CMR` `docs/`**. The trio is treated as one co-evolving unit (each
     file cross-references the other two), so it is not split across homes.
     shared-frontend remains the recorded **origin lineage** and its copies are
     a **reference mirror** of CMR; code-indexing is a **consumer** whose
     repo-adapted copies already carry GR-10 provenance pointing at CMR. Both
     stop independently evolving the trio's fleet-standard content.
   - The agent-identity standards (`agent-identity.md` + the identity schema
     set) have their **single canonical home in `kushin77/shared-governance`
     `GLOBAL_STANDARDS/`** — unchanged, now ratified.
   - `kushin77/git-rca-workspace` is the canonical home for the duplicated
     dprs/git-rca tree; `kushin77/dprs` is a **byte-identical duplicate alias** —
     harvest assets from `git-rca-workspace` only and never double-count.
2. **Product consumption and version pins.** `agent-orchestrator` consumes the
   trio from CMR through its pinned **`vendor/CMR` submodule @
   `b6c49aa03992dba9fe4b87b46104b8fc2f69f224`** (read-only reference; the
   gitlink is the version lock). It consumes the agent-identity standards by
   reference from `kushin77/shared-governance` `GLOBAL_STANDARDS/**` (analysis
   reference commit `52aeec9`, 2026-09-08). It does **not** fork any of these
   docs into the product tree outside `vendor/`.
3. **Duplicate detection is automated.** `governance/dupcheck/check-duplicates.sh`
   makes the rule mechanical (AO-GR-4 — the check can genuinely fail):
   - `scan` fails when a protected canonical doc name appears in-repo outside
     `vendor/` and `.research/`;
   - `compare <A> <B>` proves byte-identity or difference, re-verifying the
     dprs ≡ git-rca-workspace finding on demand.

## Consequences

- **Positive:** one authority per doc ends three-way drift of the trio; the
  product gets a single pinned consumption path (the `vendor/CMR` gitlink) and
  a ratifiable identity-standards home; provenance discipline (AO-GR-10) and
  the mechanical dupcheck prevent silent re-forking.
- **Negative:** CMR becomes the single edit point for trio content — shared-
  frontend and code-indexing must route their future edits there instead of
  editing their local copies (a change of habit, enforced per-repo).
- **Neutral:** existing copies stay in place as mirrors until their owners
  convert them to reference pointers; the product currently carries no forked
  copy, so no migration inside this repo is required.
- **Follow-ups:** (1) shared-frontend and code-indexing convert their trio
  copies to reference-with-provenance pointers in their own repos; (2)
  `docs/CANNIBALIZATION.md` §3.1 is updated to "resolved by ADR-0010" when that
  index is next touched by its owning lane; (3) wiring `dupcheck scan` into
  `make verify` lands under the `scripts/` lane; (4) when the phase-8
  autonomous-ops ADR-0009 lands, record the shared-governance reference pin in
  a consumption manifest.
