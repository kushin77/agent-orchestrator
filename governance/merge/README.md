# Merge / PR governance — independent SME reviewer + verify-gate (governance/merge)

> Owner lane: **autonomous-ops / governance** (issue #43, work item 39, phase
> 8). Parent: EPIC-00 (issue #4). Doctrine:
> [`AGENTS.md`](../../AGENTS.md),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md) (AO-GR-1/3/4/11/12/13/14),
> [`docs/GOVERNANCE.md`](../../docs/GOVERNANCE.md) §3,
> [`docs/QA-GATE.md`](../../docs/QA-GATE.md) (issue #29 merge gate).

This tree productizes the fleet's **merge/PR governance contract** for the
control plane: every change (human or agent) gets **green verification
evidence**, an **independent SME-persona review** (assigned, not the author),
and an **audit-trailed merge decision** — with auto-merge only after
verification + attestation. It is the governance surface of EPIC-00 phase 8
(autonomous ops / governance) and the SaaS-shaped version of the doctrine this
repo already runs on (`AGENTS.md` owner autonomous-merge mandate, 2026-09-07).

## The contract in one paragraph

A PR is driven through a deterministic state machine —
`opened → verify-gate → sme-review-assigned → approved/verified →
mergeable | blocked`. A PR becomes **mergeable only when all three hold**:

1. **Verify gate green with a commit-named attestation** — the injected verify
   check returned OK and the attestation names a concrete commit. NOT-OK and
   CANNOT-ASSESS never pass, and a green check that cannot name a commit
   cannot make a PR mergeable (an attestation names a COMMIT).
2. **Independent SME review assigned and approving** — an independent reviewer
   persona (reviewer posture, distinct from the author) approved the change.
3. **No-self-merge holds** — the merger is not the PR author, **or** the owner
   autonomous-merge carve-out is active (and the carve-out still requires the
   green evidence to be recorded: it replaces the human reviewer, never the
   evidence).

The single rule is `model.merge_verdict`; the state machine enforces it
structurally and re-checks it at the merge decision (defense in depth).
Reviewer verdicts are recorded as **evidence, not final** — a reviewer approve
never rescues a red gate.

## Why this exists

The repo's delivery doctrine (`AGENTS.md`, GOLDEN-RULES) already encodes the
owner autonomous-merge mandate and evidence-before-merge; issue #29 shipped
the executable pre-merge contract (`scripts/merge-gate.sh`); issue #11 shipped
the SME persona library and reviewer-assignment semantics. What was missing is
the **productized merge-governance policy**: a model + offline engine that
decides *when a merge is permitted* (verify-gate green + independent SME
review + no-self-merge with the owner carve-out), with an audit trail of the
decision — the layer a SaaS tenant would consume via the control plane.

## How this lane maps to the repo's doctrine (consume, never redefine)

| Doctrine source | What it says | Where this lane consumes it |
|---|---|---|
| [`AGENTS.md`](../../AGENTS.md) | Autonomous merge only after green verification evidence (GR-12); never merge failing work; verification replaces the human reviewer; no direct push to `master`; PR-gated; one-issue-one-lane | `model.merge_verdict` rule 3; `engine.MergeGovernanceEngine(owner_carve_out=True)` default; README wiring |
| [`GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md) AO-GR-3/4/11 | Verify before done; no-false-green; merge only on green verify with actual output; never merge failing work | `gate` tri-state (OK/NOT-OK/CANNOT-ASSESS); `merge_verdict` requires green; blocked PRs are terminal until a real fix |
| AO-GR-12/13/14 | Control plane never executes; independent auditor; separation of duties | engine only decides, never authors; `reviewer` posture classes + distinctness guards; auditor != executor |
| [`QA-GATE.md`](../../docs/QA-GATE.md) + [`scripts/merge-gate.sh`](../../scripts/merge-gate.sh) (issue #29) | Pre-merge contract: verify/drift/tests/negative-controls/policy-schema; refuses a dirty tree; writes `.verify/merge-attestation.json`; exit 0/1/2 | `gate.py` mirrors the tri-state + aggregation + commit-named attestation as an *injectable* offline check (additive reference — this lane does not run or edit `scripts/**`) |
| [`registry/personas`](../../registry/personas/README.md) + `mapping.py` (issue #11) | SME reviewer doctrine: assigned reviewer persona per PR/verdict; posture classes rigid; reviewer never the executor; auditor cannot execute | `reviewer.py` delegates to the real `assign_reviewer`/`assign_auditor` when importable, with an identical offline mirror fallback |

## Tree layout

```text
governance/merge/
├── README.md            # this contract doc
├── model.py             # PR state machine + merge policy (merge_verdict) + audit
├── gate.py              # verify-gate tri-state model (mirrors merge-gate.sh semantics)
├── reviewer.py          # independent SME reviewer/auditor assignment (issue #11)
├── engine.py            # offline engine composing gate + review + machine + audit
└── tests/               # pytest suite (offline)
```

## How it wires to `scripts/merge-gate.sh` (issue #29)

`scripts/merge-gate.sh run` is the **live** pre-merge contract this repo
actually runs (`make verify` green + drift + every declared pytest suite +
negative controls + policy schema, written to `.verify/merge-attestation.json`
naming the commit, exit `0/1/2`). This lane **models that gate as an injected
check** so the governance policy is testable offline:

- `gate.status_from_exit_code` mirrors the honesty tri-state (issue #28):
  `0 → OK`, `1 → NOT-OK`, everything else (e.g. `2` CANNOT-ASSESS, `124`
  timeout) → CANNOT-ASSESS. **CANNOT-ASSESS is never a pass.**
- `gate.aggregate_exit_codes` mirrors the merge-gate aggregation: any NOT-OK
  fails; any CANNOT-ASSESS keeps the gate from green; only an all-OK set
  passes.
- `VerifyOutcome.is_green` requires **both** an OK verdict **and** a
  commit-named attestation — parity with the merge gate's refusal on a dirty
  working tree ("an attestation names a COMMIT"). A PR whose gate is green
  but cannot name a commit is blocked (`verify-gate-no-commit-attestation`).

The engine consumes a `GateCheck` callable — a real deployment injects a
wrapper over `scripts/merge-gate.sh run` output; tests/demos inject green, red
and cannot-assess gates. This lane never edits or runs `scripts/**`.

## Independent SME reviewer (issue #11 semantics)

`reviewer.py` assigns the independent reviewer persona for each PR:

- **Posture classes are rigid** (`executor` | `reviewer` | `auditor`).
  `PersonaAssigner.assign_reviewer` only ever returns a reviewer-posture
  persona **distinct from the author** (a reviewer is never the executor of
  the work it reviews — AO-GR-14).
- **Auditor ≠ executor**: `assign_auditor` returns an auditor-posture persona
  distinct from the executor, and dispatching an auditor-posture persona as
  the executor (or as its own auditor) is refused — see the negative tests
  (`AuditorCannotExecuteError`, `SelfAuditError`, `SelfReviewError`).
- **Domain-scored**: the assigned persona best matches the PR subject against
  its id, owned lanes and expertise; no match falls back to the general
  `reviewer` persona (which owns the `merge-governance` lane).
- **Measured before trust**: an optional `valid` predicate models the
  validation corpus — only personas that pass it are assignable (ADR-0012 /
  leaderboard auditor-measurement doctrine: reviewer verdicts are evidence,
  not final).

When the issue #11 module (`registry/personas/mapping.py`) is importable the
assigner **delegates to it directly** (genuine consumption over the committed
platform cards); otherwise an identical offline mirror runs, so governance
never hard-depends on `jsonschema`.

## No-self-merge and the owner autonomous-merge carve-out

Fleet doctrine (AO-GR-11, GOVERNANCE.md §3, AGENTS.md): every change lands via
a PR (squash) — never a direct push — and the **owner autonomous-merge
mandate (2026-09-07)** lets an agent merge its own PR **only after green
verification evidence**. The model encodes this as a flag
(`engine.owner_carve_out`; default **on** to match the fleet mandate, modeled
explicitly so a tenant can turn it off):

- merger == author and no carve-out → blocked
  (`self-merge-without-owner-carve-out`);
- merger == author + carve-out + green verification + independent review →
  mergeable (positive test);
- merger != author → mergeable with no carve-out needed;
- carve-out + **red** gate → still blocked — the carve-out replaces the human
  reviewer, **never the evidence**.

## Audit trail of the merge decision

Every decision is append-only on the PR (`MergePr.audit`):
`verify-start → verify-pass|block`, `reviewer-assigned`,
`review-approve|block`, `merge-permitted|block`. The merge-permitted entry and
the PR record both name the **attested evidence commit**. Blocked decisions
record their closed reason (e.g. `verify-gate-not-green`,
`sme-reviewer-not-assigned`, `self-merge-without-owner-carve-out`).

## Usage

```bash
# from the repo root — offline demo (mergeable + blocked scenarios)
python3 governance/merge/engine.py demo

# the full offline test suite
python3 -m pytest -p no:cacheprovider -q governance/merge/tests
```

The suite is **not** registered in `scripts/pytest-suites.txt` (that manifest
is owned by the issue #29 lane and is drift-checked only for the
pillar prefixes it declares); it is run explicitly, exactly like the demo
above. `make verify` stays green (this lane only adds files under
`governance/merge/`).

## Provenance (GR-10)

Harvested doctrine (read-only sources; the repo-level index
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md) is owned by issue
#8, so provenance is recorded here):

| Source | Path | What it contributed |
|---|---|---|
| CMR (fleet) | `controller/auto-merge.sh`, `controller/pr-review-queue.sh`, `controller/gh-helper.sh` | Auto-merge-after-verification posture; PR review queueing; gh-helper merge mechanics |
| CMR (fleet) | `docs/decision-records/ADR-0020-solo-dev-merge-posture.md` | Solo-dev/owner merge carve-out precedent |
| CMR (fleet) | `docs/decision-records/ADR-0021-sme-reviewer-per-pr-doctrine.md` | One SME-profiled adversarial review per PR before merge eligibility |
| CMR (fleet) | `docs/decision-records/ADR-0012-module-classification-elite-decision.md` | Evidence-based trust — verdicts/classifications are evidence, not self-description |
| CMR (fleet) | `GOLDEN-RULES.md` GR-12 | Verify + report actual output before merge |
| leaderboard (fleet) | `scripts/audit/validate-auditor.sh`, `scripts/audit/auditor-measurement-registry.sh` | Auditor measured before trust (validation corpus) |
| leaderboard (fleet) | `docs/doctrine/` (brain-directive-001) | Independent auditor/verification over every merge; separation of duties |
| shared-frontend (fleet) | `docs/EXECUTION-PLAN.md` | One-issue-one-lane disjoint-file discipline |
