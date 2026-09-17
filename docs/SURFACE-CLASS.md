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

The ladder is defined in `kushin77/CMR` `docs/SOLUTION-CLASSES.md` (referenced
by [ADR-0010](decision-records/ADR-0010-canonical-copy-ownership.md)). The
vocabulary here is closed and identical to
[`governance/conformance/model.py`](../governance/conformance/model.py) — the
suite asserts the two gates cannot drift apart.

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

`surface_roots` in [`governance/conformance/surfaces.yaml`](../governance/conformance/surfaces.yaml)
names the top-level directories that are product surfaces; each that exists must
be declared or waived by name, so no surface goes unclassified by omission.

## Related

- Implements issue #351 (parent #338); widened to the git-ecosystem surfaces by
  issue #620 (parent #616, from the gap analysis of issue #608).
- Complements `scripts/check-conformance.sh` (issue #140, the issue-class gate).
- The class ladder is declared in `kushin77/CMR` `docs/SOLUTION-CLASSES.md`;
  the ladder's local canonical copy note is
  [ADR-0010](decision-records/ADR-0010-canonical-copy-ownership.md).
