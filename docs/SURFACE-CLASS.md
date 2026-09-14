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

## The declared surfaces (measured 2026-09-14, issue #351; scope widened by #590)

| Surface | Path | Declared class | Measured class | Why this rung |
|---|---|---|---|---|
| `shell` | `portal/static` | `template` | `template` | The console *client* shell this repo hosts. It is a static asset bundle with no README and no test suite of its own, so it is held at the base rung. |
| `portal` | `portal` | `pattern` | `pattern` | Contract, tests, real controls, an audit chain and a live feed are present. It declares no shared schema of its own, which is what holds it below `enterprise`. |
| `gateway` | `gateway` | `faang` | `faang` | Contract, per-package suites, real controls, an MCP audit trail + schema, and the catalog-parity gate. No live-sync module, so not `elite`. |
| `telemetry` | `telemetry` | `enterprise` | `enterprise` | Contract, four suites, real controls, an audit trail and a schema. It has no dedicated `scripts/check-*telemetry*` gate, so it stops at `enterprise`. |
| `registry` | `registry` | `faang` | `faang` | Contract, six suites, owned schemas, an append-only event log + pack attestation (audit), a drift control and the parity gate. No live-sync module, so not `elite`. |
| `module-registry` | `governance/modules` | `faang` | `faang` | The ecosystem module registry (#445, hardened by #591): contract, a suite, an owned schema for the row shape, the declared acceptance policy (`controls.yaml` + `policy.py`, **read by** `registry.py`) and an append-only audit trail of every refusal. Its dedicated gate carries it past `enterprise`; no live-sync module, so not `elite`. |
| `module-brief` | `integrations/paperclip/reporting` | `faang` | `faang` | The paperclip reporting agent's module brief (#447, hardened by #592): contract, a suite, an owned schema for the artifact, the declared claim-resolution policy (`claim-policy.json` + `policy.py`, **read by** `composer.py`) and an append-only audit trail. Its dedicated gate carries it past `enterprise`; no live-sync module, so not `elite`. |

**Scope widened by [#590](https://github.com/kushin77/agent-orchestrator/issues/590).**
`surface_roots` named only `portal`, `gateway`, `telemetry` and `registry`, so two
surfaces this program had just shipped were held to **no class at all** — the gate
never looked at them and reported OK while both sat at `pattern`. The roots
`governance` and `integrations` are now in scope and both surfaces are declared at
the rung their evidence **measures**.

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

- Implements issue #351 (parent #338).
- Complements `scripts/check-conformance.sh` (issue #140, the issue-class gate).
- The class ladder is declared in `kushin77/CMR` `docs/SOLUTION-CLASSES.md`;
  the ladder's local canonical copy note is
  [ADR-0010](decision-records/ADR-0010-canonical-copy-ownership.md).
