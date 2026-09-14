# governance/authority — repo separation, scoped admin rights, SoD, closure

Issue **#150** (EPIC-00 #144). This module is the enforcement engine for the
rule that *a repo's fleet is its own engineering team*: it controls, locks and
closes **its own** issues under **scoped** admin authority, and no fleet can act
on another repo's files, issues or state. One enterprise-level controller rolls
up across repos and is the **only** cross-repo actor.

Doctrine, blast radius and provenance: [`docs/AUTHORITY-MODEL.md`](../../docs/AUTHORITY-MODEL.md)
(the canonical write-up). This README is the module-level quickstart.

## Files

| Path | Role |
|------|------|
| `schema.json` | Real JSON-Schema (draft-07) for the authority matrix. |
| `matrix.yaml` | The **shipped binding matrix**: per-repo fleets with scoped admin rights, one `enterprise-controller` with `cross_repo: true`, fleet actors (role + posture + tier), governed work items. |
| `model.py` | The engine: schema validator, semantic invariants, `can_act`, `separation_of_duties`, `is_closed`, overlay merging. stdlib + PyYAML only, offline. |
| `isolation.py` | The two-repo isolation demonstration: a per-repo state store whose every access is gated, plus the scenario that *derives* whether the repos are isolated. |
| `controls.yaml` | The **declared behavioral controls** (scenario → expected verdict) that give the gate its teeth. |
| `cli.py` | Gate-facing CLI (tri-state exit codes). |
| `tests/` | Behavioral test suite (pytest). |

## Commands

```bash
python3 governance/authority/cli.py validate                     # schema + semantic invariants
python3 governance/authority/cli.py can-act --principal P --repo R --action A
python3 governance/authority/cli.py sod     --work-item W
python3 governance/authority/cli.py closure [--work-item W]
python3 governance/authority/cli.py isolation
python3 governance/authority/cli.py controls
python3 governance/authority/cli.py selfcheck
python3 governance/authority/cli.py matrix
bash scripts/check-authority.sh                                  # the gate (0/1/2)
python3 -m pytest governance/authority/tests -q                  # the suite
```

## Exit codes

`0` OK (ALLOW / matrix valid / every control met) · `1` NOT-OK (an explicit
DENIAL, a real document defect, a failed control) · `2` CANNOT-ASSESS (the matrix
or schema cannot be read, or a principal/repo/action is not declared).

**CANNOT-ASSESS is never a pass.** A schema keyword the engine does not
implement raises rather than being ignored, because a silently-skipped
constraint is a false green.

## The two design lines

* **Shape is the schema's job, compliance is the decision's job.** A wrong type,
  an unknown vocabulary value, a second cross-repo principal or a fleet spanning
  two repos are *document defects* (NOT-OK). An unassigned duty, a SoD collision,
  or an empty evidence output are *DENIALS* on a perfectly valid document —
  issue #150 requires exactly that split ("a missing or empty evidence field is a
  denial, never a pass").
* **Decisions are tri-state and fail closed.** An unknown principal, repo or
  action, an invalid matrix or an unreadable schema yields CANNOT-ASSESS — the
  engine says it cannot decide instead of guessing ALLOW.

## Adding a control

Add an entry to `controls.yaml` with a `kind` (`can-act`, `sod`, `closure`,
`validate`, `load`, `isolation`), the scenario's subject, the `expect` verdict,
and — to attack a document the repo would never ship — an optional `overlay`
that is deep-merged into a **deep copy** of the shipped matrix in memory only.
Then run `bash scripts/check-authority.sh`; a control whose verdict differs from
its expectation fails the gate (exit 1).

## Wiring (owned by the orchestrator, not by this lane)

This module ships **unwired** on purpose — `scripts/verify.sh` and
`scripts/pytest-suites.txt` are shared files owned by other lanes:

1. add `governance/authority` to `scripts/pytest-suites.txt`;
2. add `authority|bash scripts/check-authority.sh` to the check list in
   `scripts/verify.sh`;
3. add a row for `docs/AUTHORITY-MODEL.md` to the index in `docs/README.md`.

Until (1) lands, `scripts/check-drift.sh` stays green: it only warns for the
pillar roots it scans (`guardrails/*`, `gateway/*`, `registry/*`, `identity/*`,
`engine/*`, `telemetry/*`), and `governance/*` is deliberately outside that scan
so a lane cannot silently redden the top-level gate for everyone else.
