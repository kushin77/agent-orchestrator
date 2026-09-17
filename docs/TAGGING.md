# TAGGING — the tag authority, end to end

> **What this is.** One declared vocabulary for every tag this repository puts on
> a governed artifact, and the **derivation** from a tag set to the gates that
> tag set requires — along the channel those gates run in (PR, CI, CD,
> operations) and at the FinOps floor the work owes. The authority lives in
> [`governance/tagging/`](../governance/tagging/README.md); this document is the
> operator-facing narrative.
>
> **Why it exists.** Classification already existed in five places here (the
> conformance policy, the surface policy, the FinOps policy, the fleet
> vocabulary and the issue forms) and **none of them could tell you what a tag
> implies**. A lane could write `class:elite` and nothing connected that word to
> a gate, a tier, or a lifecycle stage. Issue [#1175](https://github.com/kushin77/agent-orchestrator/issues/1175).

## The commands

```bash
make tagging                                # the gate (also runs inside make verify)
python3 governance/tagging/cli.py lint      # judge the authority itself
python3 governance/tagging/cli.py plan --tag class:elite --tag posture:iac --tag lifecycle:release
python3 governance/tagging/cli.py matrix    # render the matrix below
python3 governance/tagging/cli.py board     # judge the board's declared tags
python3 governance/tagging/cli.py board --live --max-stale-minutes 60
python3 governance/tagging/cli.py labels    # mint the new label vocabulary on the board
python3 governance/tagging/cli.py schema --shapes
```

## The three questions a tag set answers

### 1. How good does this have to be? — the `class` rung

The CMR quality ladder, **borrowed** from
[`governance/conformance/policy.yaml`](../governance/conformance/policy.yaml)
(itself the AO reference to `kushin77/CMR docs/SOLUTION-CLASSES.md`):

```
template → class → pattern → enterprise → faang → elite
```

This authority does **not** declare a second copy. It mirrors the set and a gate
proves the two are **equal** — so raising a surface's rung, or minting a new one,
is refused by name rather than quietly accepted on one side only.

### 2. How is it delivered? — the `posture` dimension (new)

This is the dimension the operator asked for by name. It is **multi-valued**,
because these are genuinely orthogonal properties — a SaaS surface can also be
IaC-declared.

| Posture | Meaning | What it implies |
|---|---|---|
| `overall` | cross-cutting work | no extra gate; declares the dimension so it is never silently absent |
| `saas` | a multi-tenant surface | the agent-identity parity and cross-repo boundary gates, at a `pro` floor |
| `iac` | infrastructure is **declared**, never clicked (GR-5) | `make terraform`, `make tf-validate`, `make tf-fmt`, the **flag-gated-OFF** declaration, at a `pro` floor |
| `no-human-needed` | the lane must finish with **zero operator input** | the runaway guard and the dead-letter check; escalation markers are **forbidden**, not warned |
| `human-gated` | an operator gate precedes merge | `make merge-gate` |

`no-human-needed` and `human-gated` are **mutually exclusive by declaration**.
Both at once is refused by the name `posture-contradiction` — a contradiction is
not a preference, and silently picking a winner is how a plan becomes a guess.

### 3. Where in the lifecycle is it? — the `lifecycle` dimension (new)

```
plan → build → verify → release → operate → retire
```

The stage decides **which half of the delivery pipeline applies**, which is what
makes the taxonomy end-to-end instead of a label list:

| Stage | Channel | Gates |
|---|---|---|
| `plan` | pr | `make board-gate`, `make epic-focus` |
| `build` | ci | `make tests` |
| `verify` | ci | `make verify`, `make gate-coverage` |
| `release` | **cd** | `make feature-flags`, `make cloudbuild`, `make master-attestation` |
| `operate` | ops | `make monitoring-declaration`, `make fleet-state` |
| `retire` | ops | `make github-lifecycle`, `make reconcile` |

## FinOps

The `finops` tier vocabulary is **borrowed** from
[`governance/finops/policy.json`](../governance/finops/policy.json) — the same
machine authority `scripts/check-finops-chooser.sh` pins — so a lane cannot mint
a fourth tier by editing one side.

A **floor** is a floor, not a preference: a lane may escalate above it on observed
difficulty and may never start below it. The floors come from the rules:

| Tag | Floor | Why |
|---|---|---|
| `posture:iac` | `pro` | security, secrets, auth and IaC never start below the measured-audit tier |
| `posture:saas` | `pro` | multi-tenant isolation is a security boundary |
| `class:faang`, `class:elite` | `pro` | adversarial review needs the deeper tier |
| `class:enterprise` | `flash` | real rigor, reachable cheaply |
| `posture:no-human-needed` | `flash` | bounded, mechanical work — start cheap |

A tag set that declares a tier **below** its floor is refused by name
(`finops-floor-unmet`), and the floors are compared by **rank** read from
`rules.yaml`, not by guessing at string order.

## The generated matrix

Everything below the marker is generated by `cli.py matrix` and verified by
`cli.py check` — a hand-edited matrix fails the gate. Edit
`governance/tagging/rules.yaml`, then run
`python3 governance/tagging/cli.py matrix --write`.

<!-- BEGIN GENERATED TAG -> GATE MATRIX (governance/tagging/cli.py matrix) -->

| Rule | Fires when | Gates required | Channel | FinOps floor | Refuses |
|---|---|---|---|---|---|
| `posture-iac` | posture includes `iac` | `make:terraform`, `make:tf-validate`, `make:tf-fmt` | ci | pro | — |
| `posture-saas` | posture includes `saas` | `make:agent-identity-parity`, `make:cross-repo-boundary` | ci | pro | — |
| `posture-no-human-needed` | posture includes `no-human-needed` | `check:runaway-guard`, `check:dead-letter` | ci | flash | `escalation`, `operator-question` |
| `posture-human-gated` | posture includes `human-gated` | `make:merge-gate` | pr | — | — |
| `posture-overall` | posture includes `overall` | — (none) | — | — | — |
| `lifecycle-plan` | lifecycle includes `plan` | `make:board-gate`, `make:epic-focus` | pr | — | — |
| `lifecycle-build` | lifecycle includes `build` | `make:tests` | ci | — | — |
| `lifecycle-verify` | lifecycle includes `verify` | `make:verify`, `make:gate-coverage` | ci | — | — |
| `lifecycle-release` | lifecycle includes `release` | `make:feature-flags`, `make:cloudbuild`, `make:master-attestation` | cd | — | — |
| `lifecycle-operate` | lifecycle includes `operate` | `make:monitoring-declaration`, `make:fleet-state` | ops | — | — |
| `lifecycle-retire` | lifecycle includes `retire` | `make:github-lifecycle`, `make:reconcile` | ops | — | — |
| `class-enterprise` | class is at least `enterprise` | `make:conformance` | pr | flash | — |
| `class-faang` | class is at least `faang` | `make:surface-class` | pr | pro | — |
| `class-elite` | class is at least `elite` | `make:lessons`, `make:cross-reference` | ops | pro | — |

Baseline gates applied to every tag set: `make:shell-syntax`, `check:python-syntax`, `make:yaml-lint`, `make:json-lint`, `make:docs-lint`, `check:pr-contract`, `make:secrets`.

### Dimensions

| Dimension | Kind | Values | Multi | Applies to | Authority |
|---|---|---|---|---|---|
| `area` | pattern | `^[a-z][a-z0-9-]{1,40}$` | no | issue | name anchored: `governance/conformance/policy.yaml` → `required` |
| `class` | borrowed | `template`, `class`, `pattern`, `enterprise`, `faang`, `elite` | no | issue, pr, surface, release | borrowed: `governance/conformance/policy.yaml` → `ladder` |
| `epic` | pattern | `^[a-z][a-z0-9-]{1,60}$` | no | issue | declared here |
| `finops` | borrowed | `flash`, `pro`, `auditor` | no | issue | borrowed: `governance/finops/policy.json` → `vocabulary.tiers` |
| `gdc` | closed | `enterprise` | no | issue | declared here |
| `lifecycle` | closed | `plan`, `build`, `verify`, `release`, `operate`, `retire` | no | issue, pr | declared here |
| `phase` | closed | `0-foundations`, `1-agent-registry`, `2-model-gateway`, `3-state-machine`, `4-guardrails-security`, `5-observability-finops`, `6-identity-rbac`, `7-control-plane`, `8-autonomous-ops` | no | issue | declared here |
| `pillar` | closed | `registry-profiling`, `model-gateway`, `state-machine`, `guardrails-security`, `observability-finops`, `identity-rbac`, `control-plane`, `autonomous-ops`, `governance` | no | issue | declared here |
| `posture` | closed | `overall`, `saas`, `iac`, `no-human-needed`, `human-gated` | yes | issue, pr | declared here |
| `priority` | closed | `P0`, `P1`, `P2`, `P3` | no | issue | name anchored: `governance/conformance/policy.yaml` → `required` |
| `source` | closed | `cannibalized`, `decision` | no | issue | declared here |
| `type` | closed | `feature`, `bug`, `task`, `spike`, `research`, `epic`, `automation`, `governance` | no | issue | name anchored: `governance/conformance/policy.yaml` → `required` |

<!-- END GENERATED TAG -> GATE MATRIX -->

## The gate can fail — and here is the proof

`scripts/check-tagging.sh` is tri-state (`0 OK / 1 NOT-OK / 2 CANNOT-ASSESS`,
and `CANNOT-ASSESS` is never a pass) and it is wired into `make verify` by
auto-discovery (`scripts/verify.sh`, #698), so it is enforced the moment it
lands. It runs **five** checks:

| Check | What it proves |
|---|---|
| `tagging-lint` | the authority's own shape, every borrowed vocabulary's **equality** with its authority, every rule's `when` clause, every gate name's **resolution**, every document against its frozen shape, and the declared controls against the authority they govern |
| `tagging-matrix` | the matrix below is the one the authority generates |
| `tagging-suite` | the module's pytest suite |
| `tagging-refusals` | every one of the **11** declared refusals is provoked by a real mutant, **by code and by the token the taxonomy promises it names**, with its clean twin accepted |
| `tagging-artifacts` | the frozen shapes, the ledger and the live projection round-trip — each driven with its provoked half *and* its clean twin |

The refusals check is the one that matters most, because a gate that cannot fail
is a formality (GR-12). It asserts three things at once: the mutant actually
landed (by sha256, so a no-op mutation cannot be reported as a passing control),
the refusal fires **by the name the taxonomy promised**, and the clean twin of
that input is refused **nothing** — so a rule that fires on everything is caught
rather than passing as strict.

## The artifacts around the authority

| Artifact | What it does |
|---|---|
| `controls.yaml` + `policy.py` | The required/recommended sets, the refusal severity, the FinOps rank ladder, the contradiction pairs and the limits are **restated** in the controls and compared against the taxonomy, the rules and `model.FINOPS_RANK`. Restating rather than reading is deliberate: a control that reads its own input cannot disagree with it. Relaxing one side is refused by name (`control-required-mismatch`). |
| `tagging.schema.json` + `schema.py` | The frozen shapes of the taxonomy, the rules, the controls and one ledger row, applied through the repository's **stdlib-only** subset validator, so the gate stays offline. |
| `ledger.py` | The append-only decision ledger at `.fleet/tagging/ledger.jsonl` (gitignored runtime state, overridable with `AO_TAGGING_LEDGER`). Every plan derived and board judged is one validated row; a malformed row is refused **before** the write, and a hand-written garbage line is **reported by line number** rather than raising — a ledger you cannot read past is a ledger you cannot audit. |
| `live.py` | The live projection, read-only and offline: coverage, which dimensions are in use, which declared ones are unused, and every item the taxonomy **refuses, by code and by name**. Refusals (errors) and deviations (warnings) are counted in **separate** buckets, because a report that calls a "should declare" a refusal is a report nobody reads. |

## What the system already found

Run against the live board snapshot on the day it landed, the projection named
two real defects that five separate classification surfaces had never reported:

```
$ python3 governance/tagging/cli.py board
  ERROR  unknown-value   dimension 'pillar' is single-valued but carries 2 values
                         (autonomous-ops, control-plane)  [issue-878]
  ERROR  unknown-value   dimension 'pillar' is single-valued but carries 2 values
                         (autonomous-ops, governance)     [issue-1028]
  WARNING required-missing  target 'issue' should declare 'lifecycle' (85 items)
```

Two epics carry **two pillars each** on a single-valued dimension — a board defect
that is invisible until something declares the dimension closed, which nothing had
done. The 85 `required-missing` deviations are the two dimensions this issue adds,
reported as deviations exactly as the calibration intends: the board predates
them, and enforcing them outright would paint the gate red for a reason no lane
can fix by working its own issue.

## What this does *not* yet do

Stated plainly, because a gap named is a gap that can be closed:

- `area` is governed by **shape** (`^[a-z][a-z0-9-]{1,40}$`), not by a closed
  set. Areas are minted as surfaces appear and an offline gate cannot see a label
  that does not exist yet; claiming a closed set here would be a lie the first
  time someone adds one. The authority says so in the file rather than implying a
  rigor it does not have.
- **Nothing enforces this on issue creation yet.** `governance/conformance/filing.py`
  derives the conformance labels a new issue carries; it does not yet derive the
  `posture`/`lifecycle` labels, so the deviations above will keep accruing until a
  filing path derives them. That is the natural next lane, and it is deliberately
  not claimed here.
- `board` validation reads the **offline snapshot** (`.board/snapshot.json`), so
  it needs `python3 governance/dispatch/cli.py snapshot --from-github` to be
  fresh. It is deliberately *not* in the default `make verify` path, which stays
  network-free; `--max-stale-minutes` is the one place the staleness policy lives,
  and it refuses rather than reporting a stale board as a clean one.
- The label vocabulary must be **minted on the board** before the new dimensions
  can be used: `python3 governance/tagging/cli.py labels` emits the exact
  `gh label create` commands.
