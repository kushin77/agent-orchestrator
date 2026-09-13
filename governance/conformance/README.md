# Conformance — CMR class, pattern and template enforcement (issue #140)

Every item of repository work is classified against the CMR quality ladder, and the
class it declares has to hold. This module is the enforcement: it checks the board
and it checks a change set, and it fails the gate on the failures that matter.

## The ladder

```
template → class → pattern → enterprise → faang → elite
```

Declared on an issue as a `class:<rung>` label. The IaC mandate applies at every
rung: infrastructure is declared, never clicked, and new infrastructure ships
flag-gated OFF.

## What is enforced, and what is only reported

The distinction is deliberate. A gate that fails on a standard the existing board
already violates goes red for reasons no lane can fix by working its own issue — so
enforcement is scoped to what a lane controls, and deviations are reported with
remediation.

| Check | Code | Severity |
|---|---|---|
| Milestoned issue declares no class | `class-missing` | **error** |
| Declared class is not a rung of the ladder | `class-unknown` | **error** |
| More than one class on one issue | `class-ambiguous` | **error** |
| Missing `type:` / `priority:` / `area:` | `classification-incomplete` | **error** |
| Declared class expects a label it lacks | `class-expectation-unmet` | warning (error under `--strict`) |
| Un-milestoned backlog, out of scope | `scope-mismatch` | warning |
| Change set adds a GitHub Actions workflow | `iac-mandate-unmet` | **error** |
| Change set adds infrastructure with no flag | `iac-mandate-unmet` | **error** |
| Changed package with no declared test suite | `dependency-missing` | warning |

Scope is **open issues that belong to a milestone**. A milestoned issue is a
commitment to a standard, so classification is enforced there. The un-milestoned
backlog predates the convention; it is counted, not failed, unless
`--include-unmilestoned` asks for it.

## Usage

```bash
# Board conformance — the gate of record (exit 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS)
python3 governance/conformance/cli.py check

# One milestone, with deviations escalated to errors
python3 governance/conformance/cli.py check --milestone "M24 - ..." --strict

# The current diff against the mandates
python3 governance/conformance/cli.py change-set --base origin/master

# The declared policy
python3 governance/conformance/cli.py policy
```

`make conformance` runs the check; `make verify` runs it as the 12th check via
`scripts/check-conformance.sh`.

## Calibration (measured 2026-09-13)

113 issues in the snapshot, 33 open, 20 open and milestoned.

- `class`, `type`, `priority`, `area` are declared on **20 of 20** milestoned issues,
  so enforcing them is green today and binds new work.
- The per-class expectations are **partially** satisfied — `pillar` 10/20 and `gdc`
  15/20 — which is why they are deviations rather than errors. The six current
  deviations are `#143`, `#150` (elite without `pillar`) and `#163`–`#166` (without
  `gdc`), and they are assigned to a remediation issue rather than silently ignored.

## Change-set checks

Two things are constrained, both mechanically:

1. **Automation stays code-native** (fleet GR-15). Adding `.github/workflows/*` is an
   error: the Makefile target plus the ops runner is the supported path.
2. **New infrastructure is declared OFF.** A newly added path under `infra/` must name
   its flag (`enable_`, `_ENABLE`, `disabled`, `gated`). Only *creation* is
   constrained — editing existing declarations is normal work.

Path arguments are repository-relative, which is what git reports.

## Layout

| File | Role |
|---|---|
| [`policy.yaml`](policy.yaml) | the ladder, required metadata, per-class expectations, mandates |
| [`model.py`](model.py) | ladder, findings, report |
| [`checker.py`](checker.py) | board and change-set checking |
| [`cli.py`](cli.py) | check / change-set / policy / report |

To change the standard, edit [`policy.yaml`](policy.yaml) — it is the single
declaration of what conformance means. No code change is needed to add a rung or an
expectation, and a policy naming a rung that does not exist is rejected as malformed.

## Related

- Implements issue #140 (milestone M24), parent #138, blocked-by #139.
- The class ladder and the IaC mandate are declared in
  [`../../GOLDEN-RULES.md`](../../GOLDEN-RULES.md) and
  [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).
