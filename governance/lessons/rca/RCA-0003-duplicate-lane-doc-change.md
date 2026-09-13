# RCA-0003 — two lanes shipped the same change

| Field | Value |
|---|---|
| RCA id | `RCA-0003` |
| Incident | `INC-0003` |
| Origin | `#155` — the first of the two duplicate PRs |
| Severity | `medium` |
| Owner | governance lane |
| Reviewed | `2026-09-13` |

## Impact

Two agents worked one issue in parallel and opened PR #155 and PR #156. Both
shipped the **same** 53-line change to `AGENTS.md`, `docs/GOVERNANCE.md` and
`docs/EXECUTION-PLAN.md`; the second merge was pure waste, and the two PRs
landing back-to-back made `master` move twice for one change while a third lane
was mid-task on the same files.

## Detection

Post-hoc, by auditing the merge history:
`git diff e78f80b 0ba7a2c -- AGENTS.md docs/GOVERNANCE.md docs/EXECUTION-PLAN.md`
is empty (0 lines) — the two commits are byte-identical for those files. No
gate reported anything while the duplication was in flight.

## Root cause

Nothing failed when two agents claimed one issue. The rule "one issue = one
lane = one branch" (GR-3) existed only as prose in the contract documents, and
the declaration gate introduced by PR #153 asserted that the rule was
*declared*, not that the work was *unclaimed*. A declaration check cannot detect
a race; only a lock can.

## Corrective actions

- `CA-0005` — a claim is now recorded and validated before work starts:
  `python3 governance/dispatch/cli.py claim --issue <n> --agent <id> --lane
  <lane>`, landed with issue #157 in PR #159 (`ca63611`). An in-flight issue is
  locked; a second claim fails loudly.

## Lessons

`LESSON-0003` — a rule that only exists in prose is advisory; concurrent lanes
need a claim lock, not a declaration gate. Before starting work, check the
claim ledger, the local worktree and the remote branch refs — and name lane
scratch directories uniquely.

## Evidence

- `git diff e78f80b 0ba7a2c -- AGENTS.md docs/GOVERNANCE.md docs/EXECUTION-PLAN.md`
  → 0 lines (byte-identical).
- PR #159 — `ca63611`, `feat(governance): enforce issue order at claim time
  (issue #157)`.
- Issue #170 — `Harden the claim gate: snapshot staleness check + conflict-free
  claim storage` (open).

## Follow-up

The claim lock (`CA-0005`) closed this incident. The wider claim-gate hardening
that the same audit demanded — a snapshot-staleness check and conflict-free
claim storage — is issue #170, recorded as `CA-0007` on `RCA-0005`. A softer
improvement, publishing the claim to the fleet steering channel so overlap is
visible before a pull request opens, is recorded as `SUGGEST-0003`.
