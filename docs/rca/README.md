# RCAs — operational incident writeups

Root-cause analyses for operational incidents (a wedge, a collision, a gate
that stayed quiet when it should have alerted) that are worth a durable
writeup even when they do not yet have board-resolvable issue/PR refs for
the `governance/lessons` ledger (see `governance/lessons/README.md` for the
repo's canonical, ledger-backed RCA pipeline — use it instead of this
directory whenever the incident has real refs to record).

Filename: `docs/rca/<yyyy-mm-dd>-<slug>.md`.

## Index

- [`2026-09-16-pr-queue-clearing.md`](2026-09-16-pr-queue-clearing.md) — the
  zero-byte gate-lock wedge (RCA-0007) and the shared-core-file collision
  class (RCA-0008) from a 17-PR queue-clearing session.
- [`2026-09-21-pr-release-lifecycle.md`](2026-09-21-pr-release-lifecycle.md) —
  the PR lifecycle gap report (epic #1669): stale merge trains, shared-index
  collisions, box-state gates redding every lane, hand-typed PR bodies, and
  the templated issue → merge → close-out state machine that replaces them.
  Outcome: the epic closed 2026-09-21 under the single-developer method —
  squash guard plus squash merge, `make land PR=N`, `scripts/pr-body.sh`,
  `make docs-index`, and lane venue kept code-only.
- [`2026-09-21-integrations-e2e-review.md`](2026-09-21-integrations-e2e-review.md) —
  the four-runtime (Paperclip/Hermes/DeepSeek/Claude) e2e review for epic
  #1268: per-integration frontend/backend/middleware status table, the
  tier-space/capability-space router split (#1701), and the dependency-ordered
  roadmap to e2e complete.

The real-tree quarantine (governance/reconcile/real-tree-quarantine.json) held
7 residual exemptions tracked while open; the earlier landing chain retired
all 7 into the reviewed baseline, so the quarantine array is empty on the
default branch.
