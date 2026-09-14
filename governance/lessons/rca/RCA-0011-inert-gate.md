# RCA-0011 — a gate nobody invokes is inert, and it reports nothing

| Field | Value |
|---|---|
| RCA id | `RCA-0011` |
| Incident | `INC-0011` |
| Origin | `event: inert-gate-2026-09-14` — the committed board snapshot does not carry #725 (see [`RCA-0012`](RCA-0012-stale-snapshot-no-trigger.md)) |
| Severity | `high` |
| Owner | gate lane — issue #725 (EPIC #708) |
| Reviewed | `2026-09-14` |

## Impact

Gates that exist but are invoked by nothing grant assurance they do not provide.
Measured on this tree at delivery time:

```text
grep -c check-negative-controls.sh scripts/verify.sh   -> 0
grep -c check-policy-schema.sh     scripts/verify.sh   -> 0
grep -c check-pr-contract.sh       scripts/verify.sh   -> 0
```

Three delivered checks are named by no gate, and `check-drift.sh` appears in
`scripts/verify.sh` only inside a comment. The record says the gate ran; three
of its checks did not. The failure mode is not a red gate, it is **a green gate
that is missing a limb** — and `check-gate-coverage.sh` measured the class
growing from 0 to 8 unwired check scripts in a single day when EPIC #499 landed
six of them at once. A class that can grow silently with no detector is the whole
argument for the control.

## Detection

By measuring the *wiring* — comparing the delivered set of `scripts/check-*.sh`
against the set that the gate files invoke by path. This is precisely the shape
of the gap: an unwired check produces **no output at all**, so no gate can fail
on it, and no log records its absence. The measurement must come from outside
(the filesystem plus the gate files), never from the check itself.

## Root cause

Gate coverage in this repository is **opt-in**. `scripts/verify.sh` holds an
explicit `checks=()` array, so:

- delivering a check script and *gating* with it are two different acts, and only
  the second one binds;
- nothing compared the delivered set to the invoked set until
  `check-gate-coverage.sh` (issue #526) was built — and that check was itself
  provoked into existence by EPIC #524's RCA;
- the same shape recurs outside bash: `scripts/pytest-suites.txt` declares 83
  suites while the gate names 12, so 71 suites run only because a manifest sweep
  reaches them — a declared artifact is not a covered artifact.

An inert check is strictly worse than an absent one, because the delivery record
counts it as a control. That is GR-12's formality with extra steps: the rule
exists, cannot fail, and is cited as evidence.

## Corrective actions

- `CA-0013` — extend the gate-coverage rule to the **newly delivered** case
  (issue **#725**): a new `scripts/check-*.sh` that no gate invokes by path fails
  immediately, naming the path, and is never grandfathered into the baseline. A
  baseline row for a newly delivered artifact is refused, and stale rows fail, so
  the exception list cannot rot into a permanent excuse.
- This delivery is subject to its own rule: `scripts/check-infra-limits.sh`
  (#729) is unwired at its delivery commit, and `check-gate-coverage.sh` names it
  until the owner of `scripts/verify.sh` registers it. The refusal working on the
  very lane that records it is the evidence the control is real.

## Lessons

`SUGGEST-0009` — **delivery is not coverage: an artifact that no gate invokes is
inert.** Every delivered check must be named by path from a gate file, and the
comparison between "delivered" and "invoked" belongs to a gate, not to a review;
a control that is cited as evidence while nothing runs it is the most dangerous
kind of green. Recorded as an open suggestion until #725 lands.

## Evidence

- Measured 2026-09-14: `grep -c` → `0` references in `scripts/verify.sh` for
  `check-negative-controls.sh`, `check-policy-schema.sh` and `check-pr-contract.sh`;
  `check-drift.sh` present only in a comment.
- `bash scripts/check-gate-coverage.sh` → the explicit gate-invocation universe
  (five gate files, literal, never a glob), the delivered-vs-invoked comparison,
  and the provenance requirements on every baseline row.
- Ledger `INC-0003` is the duplicate-work analogue: two lanes shipped one change
  because nothing compared who was doing what. Same mechanism, different subject.
- Ledger: `INC-0011`, `RCA-0011`, `CA-0013`, `SUGGEST-0009`.

## Follow-up

The incident closes with `CA-0013` (#725). Until then this repository carries at
least three unwired checks by measurement, and the count is a fact of the tree
rather than an opinion about it.
