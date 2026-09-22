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
- [`2026-09-21-isolation-lane-release-1603.md`](2026-09-21-isolation-lane-release-1603.md) —
  #1603's four merged lanes have no live `.fleet/lanes` record anywhere to
  release: two were already reaped, two were never opened as isolation
  lanes. The Done line is satisfied vacuously.
- [`2026-09-21-gr5-enabled-by-default.md`](2026-09-21-gr5-enabled-by-default.md) —
  the GR-5/AO-GR-6 policy reversal (owner decision 2026-09-21): new
  capabilities ship enabled by default instead of flag-gated off. Full flip
  list, the CloudBuild and paperclip exceptions, and hardcoded-old-default
  test fixes.
- Epic #1908 (codebase hygiene: headers, tagging, wrappers, env-vars,
  templates) closed 2026-09-21: 6 of 7 children closed; #1915 (route
  undeclared plain-config env reads through the flags/config aggregators,
  155 sites) left open as documented backlog. The header-debt ledger
  (`scripts/code-headers-baseline.tsv`) remains 288 recorded rows (270
  excused, 18 drifted, advisory at tier 0) — organizational P2 debt tracked,
  not cleared; not a gate blocker.
- [`2026-09-21-reconcile-orphan-triage.md`](2026-09-21-reconcile-orphan-triage.md) —
  #1602's 40 `closeout-blocked` lanes, re-measured: none is reclaimable (0
  return `"ok": true`), the `pr-merged` group carries **closed-unmerged** PRs
  rather than a trailer gap, and 20 lanes hold unlanded work that rule 17
  forbids trading away. Budget stays red (`orphan-issue-lane` 53/25, up from
  42).

The real-tree quarantine (governance/reconcile/real-tree-quarantine.json) held
7 residual exemptions tracked while open; the earlier landing chain retired
all 7 into the reviewed baseline, so the quarantine array is empty on the
default branch.

The shared shell library (scripts/lib/common.sh) landed in two waves and now
covers every scripts/*.sh script except the gate of record itself
(verify.sh, check-gate-lock.sh), which keep their own reviewed copy.

Issue 1760's Done line was already met by the settings aggregator landed the
same day (commit 25aad1be): dispatch tier policy, provider flags, and the
Nous secret declaration all project through the standard schema, tests
pinned in portal/tests/test_settings_aggregator.py.

Issue 1759's Done line was already met by the same settings aggregator: the
gate skip-budget domain projects scripts/skip-budget.json rows via the
standard schema, tests already pin it.

EPIC #1934 (end-to-end compliance closure) closes out: its two real children
are both fixed. #1936 (portal/config/feature-flags.yaml still gating
org_chart/skill_studio/task_board off despite the registry's GR-5 reversal)
was fixed by PR #1954 — `portal/config/feature-flags.yaml` now reads
`default: on` for all three surfaces, matching
`infra/feature-flags/registry.yaml`, with a new cross-check rule in
`scripts/check-feature-flags.py` refusing future drift between the two
files. #1937 (batched `pytest gateway governance` false-failing on
basename-collision imports) was fixed by PR #1959 as a collision guard
rather than a full clean-collection fix: `scripts/check-pytest-basename-collisions.sh`
now reports `check-pytest-basename-collisions: OK — no new collisions
beyond baseline`, so the pre-existing collision set is pinned and any new
one fails the gate, while the sanctioned per-suite runner
(`scripts/check-pytest-suites.sh`) remains the correct way to run these
directories.

Issue #1738 (live-instance observation of the Sessions operator controls,
PR #1735/#1564) is confirmed real end to end, no defect found: with
`scripts/portal-demo.py` running and a seeded `.fleet/claims/*.json` row, a
real `POST /api/sessions/control/pause` against a live `claude-fleet` row
delegated through the existing remote-control family to the real
`fleet/control.py#pause` lever, which wrote a `control:pause` directive to
`.fleet/inbox/`; the real dispatcher loop (`fleet/terminal.py run`)
consumed it next cycle, wrote `.fleet/paused`, and its own heartbeat
(`.fleet/sister.heartbeat.json`) flipped to `"state": "paused"` — the loop
itself reports paused, not just the control API. The RC-4 audit rail
(`.fleet/slog.jsonl`) recorded the effect (`fleet.pause applied by
user:root@platform.example.com`). Resume was verified the same way,
clearing `.fleet/paused`. Full evidence (API response, inbox directive,
loop log, heartbeat, audit record) posted as a comment on #1738.
