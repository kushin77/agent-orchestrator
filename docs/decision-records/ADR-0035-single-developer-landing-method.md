---
id: ADR-0035
status: live
date: 2026-09-21
deciders: [owner]
req: []
supersedes: [ADR-0028]
live_resource: scripts/check-squash-message.sh; AGENTS.md "Landing a PR"
---

# ADR-0035: The single-developer landing method replaces the commit-status gate

## Status

`live` — `scripts/check-squash-message.sh` and the autonomous-merge mandate in
`AGENTS.md` ("Landing a PR", GR item 10) are the operative landing method today.

## Context

ADR-0028 made `ao/gate-of-record` a *required* commit status on `master`,
enforced through a merge-train / PR-runner mechanism. The owner mandate of
2026-09-07, revised 2026-09-21 for the single-developer method, retired merge
trains and the PR runner: `ao/gate-of-record` is no longer a required status
check on `master` (measured 2026-09-21, referenced from ADR-0028's own
banner). An agent now lands a PR by running
`scripts/check-squash-message.sh --pr N` and then `gh pr merge N --squash`;
`make verify` on a PR branch is code-only and advisory, and box-state checks
(real-tree drift, board-snapshot age, gate-lock leftovers, etc.) moved to the
attestation venue, `make master-attestation` (#1673, #1676).

## Decision

The landing method described in `AGENTS.md` "Landing a PR" — individual PRs,
squash-message check, autonomous merge, `make master-attestation` for
box-state — is the record of how a PR reaches `master`. This ADR gives that
already-adopted method an ADR home and formally supersedes ADR-0028's
commit-status/merge-train mechanism, closing the "superseded but no successor
ADR" gap that `scripts/check-adr-status.sh` would otherwise flag.

## Consequences

- **Positive:** ADR-0028's own "Superseded" banner now names a real
  successor record instead of only `AGENTS.md` prose, so `superseded_by:` is
  checkable.
- **Negative:** none — this documents a decision already in force.
- **Neutral:** `ao/gate-of-record` and the merge-train tooling remain
  described in ADR-0028 as history; nothing is deleted.
