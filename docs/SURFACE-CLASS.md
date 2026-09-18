# Per-surface target solution-classes

Every product surface this repo ships declares the rung of the CMR quality
ladder it is held to, and a gate fails while a surface sits **below** that
declaration. This is the surface counterpart to the issue-class gate in
[`governance/conformance/`](../governance/conformance/README.md) (issue #140):
that gate classifies a piece of *work*, this one classifies a *surface*.

## The ladder

```
template → class → pattern → enterprise → faang → elite
```

The ladder is defined in `kushin77/CMR` `docs/SOLUTION-CLASSES.md` and is
consumed here **by pin, never by copy**
([ADR-0031](decision-records/ADR-0031-solution-class-ladder-pin-and-class-ceilings.md),
reaffirming [ADR-0010](decision-records/ADR-0010-canonical-copy-ownership.md) §2):
the pinned text is `vendor/CMR/docs/SOLUTION-CLASSES.md` at the `vendor/CMR`
gitlink recorded as `bundle_ref` in `cmr-pin.yaml` (`scripts/check-cmr-pin.sh`).
The vocabulary here is closed and identical to
[`governance/conformance/model.py`](../governance/conformance/model.py) —
`governance/conformance/tests/test_surfaces.py::test_ladder_is_identical_to_the_issue_policy`
asserts the surface ladder and the issue ladder are the same tuple, and
`test_real_policy_loads` pins that tuple to the six CMR rungs, so the two gates
cannot drift apart or away from the pin. A local copy of the ladder is refused
by `governance/dupcheck/check-duplicates.sh scan` (`SOLUTION-CLASSES.md` is a
protected canonical doc name); a ladder change arrives only through a submodule
bump + re-pin.

## The declared surfaces (measured 2026-09-14, issue #351; scope widened by #590 and #620; four `governance/` surfaces raised to `elite` by #885)

| Surface | Path | Declared class | Measured class | Why this rung |
|---|---|---|---|---|
| `shell` | `portal/static` | `template` | `template` | The console *client* shell this repo hosts. It is a static asset bundle with no README and no test suite of its own, so it is held at the base rung. |
| `portal` | `portal` | `pattern` | `pattern` | Contract, tests, real controls, an audit chain and a live feed are present. It declares no shared schema of its own, which is what holds it below `enterprise`. |
| `gateway` | `gateway` | `faang` | `faang` | Contract, per-package suites, real controls, an MCP audit trail + schema, and the catalog-parity gate. No live-sync module, so not `elite`. |
| `telemetry` | `telemetry` | `enterprise` | `enterprise` | Contract, four suites, real controls, an audit trail and a schema. It has no dedicated `scripts/check-*telemetry*` gate, so it stops at `enterprise`. |
| `registry` | `registry` | `faang` | `faang` | Contract, six suites, owned schemas, an append-only event log + pack attestation (audit), a drift control and the parity gate. No live-sync module, so not `elite`. |
| `module-registry` | `governance/modules` | `faang` | `faang` | The ecosystem module registry (#445, hardened by #591): contract, a suite, an owned schema for the row shape, the declared acceptance policy (`controls.yaml` + `policy.py`, **read by** `registry.py`) and an append-only audit trail of every refusal. Its dedicated gate carries it past `enterprise`; no live-sync module, so not `elite`. |
| `module-brief` | `integrations/paperclip/reporting` | `faang` | `faang` | The paperclip reporting agent's module brief (#447, hardened by #592): contract, a suite, an owned schema for the artifact, the declared claim-resolution policy (`claim-policy.json` + `policy.py`, **read by** `composer.py`) and an append-only audit trail. Its dedicated gate carries it past `enterprise`; no live-sync module, so not `elite`. |
| `github` | `.github` | `template` | `template` | The repository's GitHub surface: the issue forms (`.github/ISSUE_TEMPLATE/`), the PR template and `dependabot.yml`. Their substantive gates are `scripts/check-issue-template.sh` and `scripts/check-pr-contract.sh`, but the ladder's `tests` and `contract` evidence is directory-shaped — there is no suite and no `README.md` under the path — so the measured rung is the base one. |
| `commit-contract` | `.gitmessage` | `template` | `template` | The commit-message contract (issue #5) parsed by `scripts/check-pr-contract.sh`. The path is a single file, so `tests` and `contract` (measured as artifacts *under* the path) cannot exist; the measured rung is the base one. |
| `dispatch` | `governance/dispatch` | `elite` | `elite` | Claim/order/dispatch governance (golden rule 14): contract, a suite and the `scripts/check-chronological-dispatch.sh` gate, plus the four `elite` artifacts landed by #1062 (issue #885): `controls.yaml` + `policy.py` (read by `snapshot.py`'s `DEFAULT_STALENESS_MINUTES`), the append-only arbitration audit trail (`audit.py`), the frozen record shapes (`dispatch.schema.json` + `schema.py`), and the live projection `live.py` (`status --live`). |
| `isolation` | `governance/isolation` | `elite` | `elite` | Session identity and lane isolation (rule 15): contract, a suite, the `scripts/check-session-isolation.sh` gate and the audit module (`audit.py`) the instrumentation reads, plus the four `elite` artifacts landed by #1066 (issue #885): `controls.yaml` + `policy.py` (read at import time by `identity.py` and `speculative.py`), the append-only audit-verdict journal (`journal.py`), the frozen record shapes (`isolation.schema.json` + `schema.py`), and the live projection `live.py` (`audit --live`). |
| `lifecycle` | `governance/lifecycle` | `elite` | `elite` | End-to-end closure (rule 16): contract, a suite, the `scripts/check-github-lifecycle.sh` gate and `audit.py`, plus the four `elite` artifacts landed by #1065 (issue #885): `controls.yaml` + `policy.py` (checked against `model.py`'s invariant vocabulary), the decision ledger (`ledger.py`), the frozen record shapes (`lifecycle.schema.json`), and the live projection `live.py` (`status --live`). |
| `reconcile` | `governance/reconcile` | `elite` | `elite` | Orphan reconciliation (rule 17): contract, a suite and the `scripts/check-reconcile.sh` gate, plus the four `elite` artifacts landed by #1064 (issue #885): `controls.yaml` + `policy.py` (read by `sweep.py` for `max_actions_per_pass` and the closed `outcome_codes` vocabulary), the audit trail (`ledger.py`), the frozen record shapes (`reconcile.schema.json`), and the live projection `live.py` (`status --live`). |
| `tagging` | `governance/tagging` | `elite` | `elite` | The tag authority (issue #1175): contract, a suite, the `scripts/check-tagging.sh` gate, and the same four `elite` artifacts the sibling governance surfaces carry — the declared policy controls (`controls.yaml` + `policy.py`, read by the CLI's authority findings and held to the taxonomy, the rules and the declared limits, so a relaxed `required` set is refused by name), the append-only decision ledger (`ledger.py`, validated against the frozen shape **before** each write), the frozen record shapes (`tagging.schema.json`, applied through `governance/modules/schema.py` so no third-party validator is needed), and the live projection `live.py` (`board --live`). |

**Scope widened by [#590](https://github.com/kushin77/agent-orchestrator/issues/590).**
`surface_roots` named only `portal`, `gateway`, `telemetry` and `registry`, so two
surfaces this program had just shipped were held to **no class at all** — the gate
never looked at them and reported OK while both sat at `pattern`. The roots
`governance` and `integrations` are now in scope and both surfaces are declared at
the rung their evidence **measures**.

**Scope widened by [#620](https://github.com/kushin77/agent-orchestrator/issues/620)
(EPIC [#616](https://github.com/kushin77/agent-orchestrator/issues/616)).**
#590 widened the *roots*; the git ecosystem itself still had no row, so the gate
never looked at `.github/**`, at the commit contract, or at the four
`governance/` packages this repo's own rules are built on — they were held to
**no class at all** while the table read green, the same silent-scope failure
#590 recorded once. They were declared surfaces at the rung each one's own
evidence **measured** at the time: `pattern` where a contract, a suite and a
gate were all present (`governance/dispatch`, `governance/isolation`,
`governance/lifecycle`, `governance/reconcile` — since raised to `elite` by
#885, below), and the base `template` rung for the two git-ecosystem paths
whose evidence is a single artifact rather than a directory (`.github`,
`.gitmessage`). A declared row is enforcement, not documentation: declaring
any of them one rung above its measurement fails `make surface-class` **by
name**.

**Scope raised by [#885](https://github.com/kushin77/agent-orchestrator/issues/885).**
#620 declared the four `governance/` packages at `pattern` — a contract, a
suite and a gate, but no owned controls, audit trail or schema, so
`enterprise` and above were out of reach. Four lanes (#1062 dispatch, #1064
reconcile, #1065 lifecycle, #1066 isolation) each landed the same four
genuinely load-bearing artifacts — a declared acceptance policy
(`controls.yaml` + `policy.py`, read by the package's own code rather than
existing decoratively), an audit/ledger/journal trail, an owned
`*.schema.json`, and a live projection exposed through an existing verb's
`--live` flag rather than a new one — which is what carries all four past
`enterprise` and `faang` straight to `elite`, the top rung. Each package's own
gate (`scripts/check-chronological-dispatch.sh`, `scripts/check-session-isolation.sh`,
`scripts/check-github-lifecycle.sh`, `scripts/check-reconcile.sh`) provokes a
mutation of each new artifact and requires it to be refused by name. Declared
at the rung its evidence measures, never above it — the same discipline #590
and #620 established.

The declared class is **measured, never aspirational**: a surface is never
declared above the evidence its own tree shows, because raising one fails the
gate. The table above is reproducible — run `make surface-class`.

## Class ceilings (non-product rows)

Some rows cannot honestly reach the top rung because of their **shape**, not
their quality: a static asset bundle, repository metadata, a single file. The
upper rungs require artifacts *under the path* (controls, audit, schema, a
live-sync `.py`); planting them there would be decorative evidence the
no-false-green doctrine forbids. Instead such a row carries an explicit ceiling
in `surfaces.yaml` (issue #883,
[ADR-0031](decision-records/ADR-0031-solution-class-ladder-pin-and-class-ceilings.md) §c):

```yaml
  - surface: commit-contract
    path: .gitmessage
    declared_class: template
    class_ceiling: template
    ceiling_reason: >-
      The path is a single file; every rung above `template` requires an
      artifact *under* the path, which a file cannot hold. ...
```

Semantics — a ceiling is **reported, never silently waived**:

| Rule | Finding | Severity |
|---|---|---|
| a `class_ceiling` must be a rung below the top and must carry a `ceiling_reason` | policy refused (`CANNOT-ASSESS`) | policy defect |
| the ceiling and its reason are printed on **every** run | `surface-class-ceiling` | warning |
| `declared_class` above the ceiling | `surface-above-class-ceiling` | error |
| measured class above the ceiling (the ceiling has gone stale) | `surface-class-ceiling-stale` | error |
| `surface-below-declared-class` still fires under a ceiling | unchanged | error |

A row with a ceiling is a **non-product row**; every other row is a product
row. EPIC #878's "every row `elite`" is read as: every product row measures
`elite`, every non-product row measures its ceiling. The three ceilings today
(**proposed; for the owner to confirm**):

| Row | Ceiling | Why |
|---|---|---|
| `shell` (`portal/static`) | `pattern` | a README and a suite are the most a static bundle can honestly carry; controls/audit/schema/live-sync are `portal` server artifacts |
| `github` (`.github`) | `pattern` | repository metadata; its real gates live in `scripts/` and already match by name |
| `commit-contract` (`.gitmessage`) | `template` | a single file can hold nothing under it; enforced by `scripts/check-pr-contract.sh` |

## The module's declared class (`module.json`)

`module.json` declares the module's own rung in the additive key
`solution_class` (the CMR module template names no ladder field; the catalog
schema admits additional properties). Its value is the **product floor** — the
lowest measured class over the product rows (rows without a ceiling) — today
`pattern`, held by `portal`. `surfaces.py check` reads `<root>/module.json`
(or `--module <path>`), prints the floor and the declared class on every run
(`module     module.json      pattern      floor=pattern (portal)`), emits them
under `"module"` in `--json`, and refuses by name:

| Finding | Meaning |
|---|---|
| `module-class-above-floor` | `solution_class` is above the lowest measured product row (a mutant declaring `elite` is refused) |
| `module-class-undeclared` | the manifest carries no `solution_class` |
| `module-class-unknown` | `solution_class` is not a rung |
| `module-manifest-unreadable` | the manifest is not valid JSON |

The negative control is proved in
`governance/conformance/tests/test_surface_ceiling.py`
(`test_mutant_module_declaring_elite_is_refused_by_name`,
`test_real_tree_mutant_manifest_declaring_elite_is_refused_by_name`).

## The flip protocol (raising a declared class)

`declared_class` in `surfaces.yaml` is never raised in the same PR as the
evidence it depends on:

1. **Artifact PR merges** — the lane ships the real artifact (contract, suite,
   controls, audit, schema, gate, live-sync), with its own negative control.
2. **Measurement shows the rung** — `bash scripts/check-surface-class.sh`
   prints the row's *measured* class at the target rung against `master`.
3. **Flip PR raises `declared_class`** — one flip PR per wave, touching only
   `surfaces.yaml` (and this table), never bundled with artifact work; the gate
   refuses the flip by name if step 2 does not hold.
4. **`module.json` follows the floor** — `solution_class` is raised only when
   the lowest product row has itself been flipped.

## What each rung requires

Requirements are cumulative: each rung repeats every lower rung's requirement
and adds its own. Machine requirements are measured against the tree; a
`manual` requirement cannot be machine-checked and is **reported on every run**,
never silently treated as met.

| Rung | Adds |
|---|---|
| `template` | — (the path exists) |
| `class` | `tests` — a `conftest.py` or `test_*.py` under the path |
| `pattern` | `contract` — a `README.md` under the path |
| `enterprise` | `controls`, `audit`, `schema`, and the manual `rollback` |
| `faang` | `gate` — a dedicated `scripts/check-*` naming the surface |
| `elite` | `live_sync` — a live/feed/sync module under the path |

## Measured evidence vs. manual evidence

**Mechanised** (measured by
[`governance/conformance/surfaces.py`](../governance/conformance/surfaces.py),
files under `tests/` are excluded so a test cannot stand in for the artifact):

| Evidence | Measured as |
|---|---|
| `contract` | a `README.md` anywhere under the surface path |
| `tests` | a `conftest.py` or a `test_*.py` under the surface path |
| `controls` | a file whose name carries a control / controls / policy / policies / guard / limit / quota / killswitch / ratelimit / backpressure / parity token |
| `audit` | a file whose name carries an audit / auditlog / audit_event / event_log / attestation / ledger / journal token |
| `schema` | a `*.schema.json` under the surface path (outside `tests/`) |
| `gate` | a `scripts/check-*` whose file name carries the surface name |
| `live_sync` | a `.py` module whose name carries a live / feed / sync token |

**Manual** — declared in `surfaces.yaml` as `kind: manual` and reported as a
`surface-manual-requirement` finding on every run:

| Evidence | Why it is manual | Reported for |
|---|---|---|
| `rollback` | A documented rollout + rollback procedure (CMR "enterprise") is prose, not a machine-checkable artifact in this tree. | `gateway`, `telemetry`, `registry`, `module-registry`, `module-brief` |

A manual requirement is reported, never assumed met: the three surfaces above
show the open gap every run until the procedure is recorded and the requirement
is either mechanised or moved to a check that can read it.

## The gate

```bash
make surface-class                       # the gate (with its negative control)
python3 governance/conformance/surfaces.py check      # the check alone
python3 governance/conformance/surfaces.py check --json
```

`scripts/check-surface-class.sh` is tri-state — `0 OK / 1 NOT-OK /
2 CANNOT-ASSESS` (`CANNOT-ASSESS` is never reported as a pass) — and runs a
**self-mutating negative control**: it copies the policy to a scratch tree,
raises the first surface's declared class by one rung, and requires the checker
to refuse the mutant by name. If the mutant passes, the gate reports `FAIL`,
because a check that cannot fail is a formality.

## Findings

| Code | Severity | Meaning |
|---|---|---|
| `surface-below-declared-class` | error | a declared surface's machine evidence is absent |
| `surface-unknown` | error | a surface declares a class that is not a rung |
| `surface-path-missing` | error | a declared surface path does not exist |
| `surface-path-escapes-root` | error | a declared surface path is absolute or escapes the repo |
| `surface-undeclared` | error | an existing surface root no surface declares |
| `surface-duplicate` | error | a surface is declared more than once |
| `surface-manual-requirement` | warning | a declared class carries a manual requirement (reported, never assumed met) |
| `surface-class-ceiling` | warning | a row carries a class ceiling (reported on every run with its reason, never silently waived) |
| `surface-above-class-ceiling` | error | a row declares a class above its ceiling |
| `surface-class-ceiling-stale` | error | a row measures above its ceiling; the ceiling no longer describes it |
| `module-class-above-floor` | error | `module.json` `solution_class` is above the lowest measured product row |
| `module-class-undeclared` / `module-class-unknown` / `module-manifest-unreadable` | error | `module.json` has no `solution_class`, a non-rung, or is not valid JSON |

`surface_roots` in [`governance/conformance/surfaces.yaml`](../governance/conformance/surfaces.yaml)
names the top-level directories that are product surfaces; each that exists must
be declared or waived by name, so no surface goes unclassified by omission.

## Related

- Implements issue #351 (parent #338); widened to the git-ecosystem surfaces by
  issue #620 (parent #616, from the gap analysis of issue #608).
- Complements `scripts/check-conformance.sh` (issue #140, the issue-class gate).
- The class ladder is declared in `kushin77/CMR` `docs/SOLUTION-CLASSES.md`
  and consumed by pin (`vendor/CMR/docs/SOLUTION-CLASSES.md`); canon is
  [ADR-0010](decision-records/ADR-0010-canonical-copy-ownership.md), the pin,
  the class ceilings, the module's declared class and the flip protocol are
  [ADR-0031](decision-records/ADR-0031-solution-class-ladder-pin-and-class-ceilings.md)
  (issue #883, EPIC #878).
