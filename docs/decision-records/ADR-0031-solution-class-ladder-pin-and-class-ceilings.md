---
id: ADR-0031
status: proposed
date: 2026-09-17
deciders: [owner]
req: []
supersedes: []
---

# ADR-0031: Solution-class ladder by pin, trio canon reaffirmed per doc, and class ceilings for non-product surfaces

## Status

`proposed` — issue #883 (lane L4 of EPIC #878). Parts (a) and (b) **reaffirm**
canon already accepted in [ADR-0010](ADR-0010-canonical-copy-ownership.md) and
need no new ratification; part (c) introduces a new gate mechanism whose three
concrete values are **for the owner to confirm** (`deciders: [owner]`). The
record leaves `proposed` when the owner confirms the ceilings.

**Numbering note:** `0030` is the next number in sequence but is already cited
from this repo's board for a different, fleet-level record (the "lanes hand work
back uncommitted to the orchestrating lead" rule named as `ADR-0030` by #358 and
#329, recorded in [ADR-0025](ADR-0025-remote-control-transport.md) §Claims).
By the rule ADR-0025 and ADR-0028 applied — never re-point a number an existing
citation already uses — this record takes `0031`. A tree-wide grep (excluding
`vendor/`, `.research/`, `.board/`) finds zero citations of `ADR-0031`.

## Context

EPIC #878 folds three "stated gaps" into this lane:

1. **The ladder is defined in `kushin77/CMR`, not copied locally.** The
   solution-class ladder (`template → class → pattern → enterprise → faang →
   elite`) lives in `kushin77/CMR` `docs/SOLUTION-CLASSES.md`. This repo
   consumes it through the pinned `vendor/CMR` submodule (gitlink
   `b6c49aa03992dba9fe4b87b46104b8fc2f69f224`, recorded as `bundle_ref` in
   `cmr-pin.yaml`, issue #949), where the file is present at
   `vendor/CMR/docs/SOLUTION-CLASSES.md`. ADR-0010 §2 already commits the
   product to consuming the trio via that gitlink and to **not forking any of
   these docs into the product tree outside `vendor/`**, and
   `governance/dupcheck/check-duplicates.sh scan` **fails** when a protected
   canonical doc name — `SOLUTION-CLASSES.md` is in its `protected_names` list
   — appears in-repo outside `vendor/` and `.research/`. The question the EPIC
   asks ("copied or pinned?") is therefore already mechanically answered; what
   was missing is a local doc that says so and a guarantee that the vocabulary
   the gates enforce equals the pinned ladder.

2. **Trio canonicity "TO BE DECIDED".** `docs/CANNIBALIZATION.md` §3.1 still
   carried the pre-ADR-0010 heading. ADR-0010 Decision 1 places the whole trio
   (`MODEL-PROFILES.md`, `SME-PROFILES.md`, `SOLUTION-CLASSES.md`) in
   `kushin77/CMR` `docs/` as one co-evolving unit, with shared-frontend the
   recorded origin lineage and code-indexing a consumer; its own follow-up (2)
   says §3.1 gets updated "when that index is next touched by its owning lane"
   — this lane. Evidence of where each doc is actually maintained today
   (`grep -rn "MODEL-PROFILES\|SME-PROFILES\|SOLUTION-CLASSES" docs/ AGENTS.md`):

   | Doc | Where this repo reads it | Evidence of active maintenance |
   |---|---|---|
   | `SOLUTION-CLASSES.md` | `vendor/CMR/docs/` — cited by `governance/conformance/surfaces.yaml` header, `docs/SURFACE-CLASS.md`, ADR-0010 | The ladder the CMR hub's own `fleet/MANIFEST.tsv` `class` column uses; the vendored copy is the one every local gate names. |
   | `MODEL-PROFILES.md` | `vendor/CMR/docs/` (ADR-0010 §Context) | The vendor mirror at pin `b6c49aa` is **newer** than the CMR snapshot the spike measured (md5 `7b1513fd…` vs `16677c1e…`, "adds a deliberate CMR/global divergence note") — CMR is where edits land. |
   | `SME-PROFILES.md` | `vendor/CMR/docs/` — `docs/REGISTRY-PROVENANCE.md` row 33 records `kushin77/CMR docs/SME-PROFILES.md` as the REFERENCE for SME card doctrine and the lane map | code-indexing records shared-frontend as the *origin* of the persona spec "(mirrored at CMR)"; the mirror is where the fleet reads it. |

   No local copy of any of the three exists outside `vendor/`
   (`dupcheck scan` passes). Nothing in the evidence argues for splitting
   `SME-PROFILES` away from its siblings; doing so would amend ADR-0010, which
   this record does **not** do.

3. **Three declared rows cannot meaningfully reach `elite`.** `shell`
   (`portal/static`, a static asset bundle), `github` (`.github`, repository
   metadata) and `commit-contract` (`.gitmessage`, a single file) are declared
   at `template` and measure `template`. The EPIC's DoD reads "the conformance
   gate measures `elite` for every row". The ladder's upper rungs require a
   controls file, an audit module, a `*.schema.json`, a dedicated gate and a
   live-sync `.py` **under the path**; planting those under `portal/static` or
   `.github` would be decorative artifacts that the no-false-green doctrine
   (#852 ratchet) forbids, and a single file can hold nothing under it at all.
   Without an explicit mechanism, the only ways to make the EPIC's DoD true
   for those rows are to fake evidence or to delete the rows — both silent
   waivers.

4. **The module declares no class of its own.** `module.json` (`cmr.module/v1`)
   carries `class: ["backend", "service", "saas", "control-plane"]`, which the
   CMR catalog schema (`vendor/CMR/catalog/schemas/module.schema.json`)
   describes as "Free-form classification tags" — not the ladder rung. The CMR
   module template (`vendor/CMR/templates/module/module.json`) defines **no**
   ladder field at all, and the schema sets `additionalProperties: true` at
   the top level, so an additive key is admissible by the hub. The lane's
   acceptance is "every module declares its class in `module.json` and the
   conformance gate reads it: declared vs measured, no row below declared".

## Decision

**(a) Pin by reference; never copy.** The ladder's canonical text stays in
`kushin77/CMR` `docs/SOLUTION-CLASSES.md`, consumed at the pinned path
`vendor/CMR/docs/SOLUTION-CLASSES.md` (gitlink = `cmr-pin.yaml` `bundle_ref`).
The local doc `docs/SURFACE-CLASS.md` cites that pinned path and asserts that
the closed vocabulary enforced by `governance/conformance/model.py`,
`policy.yaml` and `surfaces.yaml` equals the pinned ladder. The mechanical
guards are already in place and are named as the enforcement, not re-invented:

- `governance/conformance/tests/test_surfaces.py::test_ladder_is_identical_to_the_issue_policy`
  proves the surface ladder and the issue ladder are the same tuple;
  `test_real_policy_loads` pins that tuple to the six CMR rungs; and
  `load_surface_policy` refuses any evidence key outside the closed
  `EVIDENCE_KEYS` vocabulary.
- `governance/dupcheck/check-duplicates.sh scan` refuses a forked
  `SOLUTION-CLASSES.md` anywhere outside `vendor/` and `.research/`.
- A ladder change upstream arrives only through a `vendor/CMR` gitlink bump +
  `cmr-pin.yaml` re-pin (`scripts/check-cmr-pin.sh`), never by editing text
  here (AO-GR-10 provenance; ADR-0010 §2).

**(b) Trio canonicity: reaffirmed per doc, all three in CMR.** Resolved by
ADR-0010; this record adds the per-doc evidence above and closes the §3.1
"TO BE DECIDED" heading in `docs/CANNIBALIZATION.md`. `kushin77/CMR` `docs/` is
canonical for `MODEL-PROFILES.md`, `SME-PROFILES.md` and `SOLUTION-CLASSES.md`;
`kushin77/shared-frontend` is the recorded origin lineage (a reference mirror);
`kushin77/code-indexing` is a consumer. This repo reads all three only from the
pinned `vendor/CMR/docs/` path. Any later split of the trio is a **new** ADR
that names ADR-0010 in `supersedes`.

**(c) Class ceilings — an explicit, reported, never-silent limit.** A row in
`governance/conformance/surfaces.yaml` may carry `class_ceiling: <rung>` with a
mandatory `ceiling_reason`. Semantics, implemented in
`governance/conformance/surfaces.py` (issue #883) and tested in
`governance/conformance/tests/test_surface_ceiling.py`:

| Rule | Finding | Severity |
|---|---|---|
| A ceiling must be a rung below the top; a ceiling without a reason is refused at load | `SurfacePolicyUnavailable` (CANNOT-ASSESS) | policy defect |
| A ceiling is **reported on every run** with its reason | `surface-class-ceiling` | warning (never silent) |
| `declared_class` above the ceiling | `surface-above-class-ceiling` | error |
| measured class above the ceiling (the ceiling has gone stale) | `surface-class-ceiling-stale` | error |
| `surface-below-declared-class` still fires under a ceiling (the gate's own negative control mutates the first row, a ceiling row) | unchanged | error |

Rows carrying a ceiling are, by definition, the **non-product rows**. The
EPIC's DoD is read as "every **product** row measures `elite`; every
non-product row measures **its ceiling**" — a ceiling is not a waiver of the
DoD, it is the honest upper bound the row's shape admits, stated in the policy
where the gate reports it every run. The three proposed values are for the
**owner to confirm**:

| Row | Ceiling | Why |
|---|---|---|
| `shell` (`portal/static`) | `pattern` | A README and a suite are the most a static bundle can honestly carry; controls/audit/schema/live-sync are `portal` server artifacts. |
| `github` (`.github`) | `pattern` | Repository metadata; its real gates live in `scripts/` and already match by name; anything more under `.github` would be decorative. |
| `commit-contract` (`.gitmessage`) | `template` | A single file can hold nothing under it; the contract is enforced by `scripts/check-pr-contract.sh`. |

**(d) The module declares its class and the gate holds it to the floor.**
`module.json` gains the additive key `solution_class` (a string rung; the CMR
template names no such field and the schema admits additional properties).
Its value is the **product floor**: the lowest measured class over the product
rows (every row without a ceiling) — today `pattern`, held by `portal`.
`surfaces.py check` reads `<root>/module.json` (or `--module <path>`) and
refuses, by name, a declaration above the floor (`module-class-above-floor`),
an undeclared key (`module-class-undeclared`), a non-rung
(`module-class-unknown`) and an unreadable manifest
(`module-manifest-unreadable`). The floor and the manifest's declared class are
printed on every run and emitted under `"module"` in `--json`. The negative
control — a mutant manifest declaring `elite` is refused by name — is proved in
the suite (`test_mutant_module_declaring_elite_is_refused_by_name`,
`test_real_tree_mutant_manifest_declaring_elite_is_refused_by_name`).

**(e) The flip protocol.** A `declared_class` is raised only after the evidence
is in the tree: an artifact PR merges → `scripts/check-surface-class.sh` shows
the row's measured class at the target rung → a **flip PR** raises
`declared_class` for that wave's rows (one flip PR per wave, never bundled with
artifact work) → `module.json` `solution_class` is raised only when the product
floor itself rises. No row's `declared_class` is changed by this record.

## Consequences

- **Positive:** the copy-vs-pin question has one answer with three mechanical
  guards already enforcing it; §3.1 stops contradicting ADR-0010; the three
  non-product rows have an explicit, reported ceiling instead of a silent
  exemption or a fake `elite`; the module's own class is declared in the
  manifest and cannot exceed what its weakest product surface measures.
- **Negative:** the EPIC's "every row `elite`" DoD is narrowed to product rows
  — an honest narrowing, but one the owner must confirm; three warnings are
  now printed on every gate run by design.
- **Neutral:** `--json` output gains `class_ceiling` per row and a `module`
  block; the human table gains a `ceiling` column and a `module` line.
- **Follow-ups:** (1) owner confirms or edits the three ceilings and flips this
  record to `accepted`; (2) `scripts/check-surface-class.sh` (L3's gate-contract
  lane) may add a `module.json` mutant to its shell negative control — today
  the mutant lives in pytest; (3) the ADR index row in
  `docs/decision-records/README.md` (not owned by this lane); (4) residual
  risk: a future *product* row that legitimately needs a ceiling would drop
  out of the floor — revisit `product_floor()` if that happens; (5) the flip
  waves for the sibling lanes of #878 raise `declared_class` per (e) after
  their artifact PRs merge.
