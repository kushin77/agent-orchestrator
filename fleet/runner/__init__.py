"""fleet.runner — the shared-services PR runner (issue #1343, parent #1295).

---knowledge---
module_id: fleet.runner.__init__
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

Verifies every open PR head that lacks evidence, publishes `ao/gate-of-record`
for it, and merges the greens through the guarded entrypoint
(`scripts/merge-pr.sh`) once the merged tree (master + PR) is proven green.

Layout — pure core first, transports last, exactly like
`infra/fleet/promote_portal.py`:

* `model.py`    the record types (open PR, evidence, live build, action)
* `evidence.py` ranked per-(pr, sha) evidence: cloud-build > gate-status > local
* `plan.py`     the planner: a PURE function of the inputs -> ordered actions
* `verify.py`   the verify transport: fetch lock, held worktree, gate-lock prune,
                `scripts/verify.sh`, `scripts/gate-status.sh post`
* `merge.py`    the merge transport: merged-tree seam, then `scripts/merge-pr.sh`
* `cli.py`      `plan`, `run --once|--loop`, `status`, `hold`, `unhold`

Every lesson the 2026-09-18 prototype measured is a named negative control in
`fleet/runner/tests/` and a `LESSON-*` record in `governance/lessons/ledger.jsonl`.
"""
