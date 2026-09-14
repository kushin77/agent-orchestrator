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

# The filing path derives declaring labels and refuses when it cannot
python3 governance/conformance/cli.py filing-check

# The supported hand-run filing path: the labels are derived for you
python3 governance/conformance/cli.py file --title "..." --body "..." --dry-run

# The declared policy
python3 governance/conformance/cli.py policy
```

`make conformance` runs the board check *and* the filing self-control;
`make verify` runs it as the 12th check via `scripts/check-conformance.sh`.

## Per-surface target solution-classes (issue #351)

The board check above classifies a piece of *work*. The surface counterpart
classifies a *surface*: every product surface declares the rung of the ladder it
is held to, and the gate fails while the surface sits below that declaration.
`make surface-class` runs it (wired into `make verify` right after
`conformance`); the declaration table, the measured class of each surface and
what is mechanised versus `manual` are in
[`docs/SURFACE-CLASS.md`](../../docs/SURFACE-CLASS.md).

* [`surfaces.yaml`](surfaces.yaml) — the declared surfaces, the closed evidence
  vocabulary and the cumulative per-rung requirements.
* [`surfaces.py`](surfaces.py) — the checker: measures the machine evidence,
  derives the measured class and emits stable finding codes.
* `scripts/check-surface-class.sh` — the tri-state gate, with a self-mutating
  negative control that raises a declared class beyond its evidence and requires
  the refusal by name.

## The filing path — prevention, not repair (issue #320)

A gate that only refuses an unclassified issue *after* it exists leaves the board
wrong until someone notices. The filing path is therefore part of the standard:

* Every code path that files an issue derives its declaring labels from
  [`policy.yaml`](policy.yaml) — the `class` from the filing's own declaration or
  `filing.default_class`, the companions (`type`, `priority`, `area`) and the
  declared class's expectations from `filing.defaults` — and passes them to
  `gh issue create` ([`filing.py`](filing.py)).
* A filing that **cannot** derive one of those labels is REFUSED
  (`FilingRefused`) *before* the command is built: nothing is filed, so nothing has
  to be repaired afterwards. The refusal names itself and points at the issue that
  owns prevention (#320) and the one that owns the legacy repair (#174).
* `fleet/brain.py` files micro-task issues only through that seam and reports the
  refusal to the operator instead of filing an issue the gate rejects later.
* `cli.py file` is the supported hand-run path, for the same reason.

### A declaration is honoured or refused — never dropped (issue #517)

`--declare name=value` states a companion label explicitly. A name the policy
recognises as a declaring label (`Policy.declaring_fields`: `class`, the `required`
companions, every rung's `expectations`, and the `prefixed` vocabulary — `pillar`,
`phase`, `gdc`, `source`) is **passed through** into the labels the filing carries,
even when the declared class does not require it. Before #517 anything outside the
class's own label set was accepted on the command line and then discarded — `file
--declare pillar=autonomous-ops --declare phase=8-autonomous-ops` planned five
labels and no `pillar:`/`phase:` at all, with no warning and exit 0.

A declared name the policy does **not** recognise is **refused**, naming the field,
with a non-zero exit and nothing filed. Refusal rather than pass-through, for three
reasons:

1. the doctrine on this path is already *refuse rather than drop* — the module
   refuses a filing that cannot derive a required label rather than filing one the
   gate rejects later, and an ignored declaration is the same loss in reverse;
2. `--declare` is documented as the *declaring* vocabulary, and `--label` is the
   explicit path for a label the policy does not declare — so refusing loses no
   expressiveness while catching the typo (`--declare priorty=P1`) that would
   otherwise land an issue missing the `priority:` its filer believes it declared;
3. passing an unknown name through would write unvalidated metadata onto the board,
   where nothing detects it afterwards (`expectations` are non-fatal deviations).

Two further declarations are refused for the same reason: a `class:` label that
contradicts the class the filing states (one would have to be dropped to honour the
other), and a declaration with an empty value (falling back to the policy default
would report a declaration that was never honoured). `--dry-run` plans through the
same `plan_filing`, so it prints exactly the label set a real filing would pass.

`filing-check` is the self-control the gate runs (GR-12: a control whose refusal
path cannot be reached is a formality). It provokes, for real:

| Expectation | What is provoked |
|---|---|
| labels derived from the policy | a filing that declares nothing still carries every declaring label |
| labels passed to `gh issue create` | every derived label appears as a `--label` pair |
| refuses a filing with no derivable class | `filing.default_class` removed from the policy |
| refuses a class outside the ladder | a filing declaring `platinum` |
| refuses an underivable companion | the #297 shape (a class, no `priority:`) |
| refusal files nothing | the runner is never reached |
| refusal is explicit | the message names `#320` (prevention) and `#174` (repair) |
| the fleet's filing path delegates | `fleet/brain.py` builds no `gh issue create` of its own |
| passes through a recognised companion | `pillar`/`phase` declared at `enterprise`, which does not require them (#517) |
| refuses an unrecognised declaration | a declared `priorty` (#517) |
| the unrecognised-declaration refusal files nothing | the runner is never reached (#517) |
| refuses two different classes | `--class elite` plus a declared `class:enterprise` (#517) |
| a dry run shows the filing's labels | the same plan the real filing would run (#517) |

`scripts/check-conformance.sh` adds the same two controls at the CLI boundary — the
layer the #517 defect was observed at: the labels a filing prints must include a
declared `pillar:`/`phase:`, and an unrecognised `--declare` must exit non-zero
naming the field.

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
| [`policy.yaml`](policy.yaml) | the ladder, required metadata, per-class expectations, mandates, filing defaults |
| [`model.py`](model.py) | ladder, findings, report |
| [`checker.py`](checker.py) | board and change-set checking |
| [`filing.py`](filing.py) | the issue-filing seam: derive the declaring labels, or refuse (#320) |
| [`cli.py`](cli.py) | check / change-set / filing-check / file / policy / report |
| [`surfaces.yaml`](surfaces.yaml) | the declared surfaces + the closed evidence vocabulary (#351) |
| [`surfaces.py`](surfaces.py) | the per-surface target-class checker (#351) |

To change the standard, edit [`policy.yaml`](policy.yaml) — it is the single
declaration of what conformance means. No code change is needed to add a rung, an
expectation, or a filing default, and a policy naming a rung that does not exist —
including a `filing.default_class` that is not a rung — is rejected as malformed.
A name added to `required`, `expectations` or `prefixed` is also a name
`--declare` will accept; a name in none of them is refused (#517).

## Related

- Implements issue #140 (milestone M24), parent #138, blocked-by #139.
- The filing path (issue #320) derives declaring labels for every filed issue;
  [#174](https://github.com/kushin77/agent-orchestrator/issues/174) owns repairing
  the legacy issues that were filed before it.
- The class ladder and the IaC mandate are declared in
  [`../../GOLDEN-RULES.md`](../../GOLDEN-RULES.md) and
  [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).
