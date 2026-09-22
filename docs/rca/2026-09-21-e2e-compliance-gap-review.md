# 2026-09-21 — end-to-end compliance gap review

Epic: kushin77/agent-orchestrator#1940

## Scope

Full gap analysis against the ~26 open issues at review time (`gh issue list
--state open`), verified against actual code rather than issue titles, plus
direct test/flag evidence for the frontend (portal), backend
(gateway/governance), and middleware (governance/dispatch) layers.

## Test evidence

| Layer | Command | Result |
|---|---|---|
| Frontend | `python3 -m pytest portal/tests -q` | **603 passed** in 155.6s |
| Backend | `python3 -m pytest gateway/proxy gateway/limits gateway/providers -q` (per-dir) | **79 + 87 + 146 = 312 passed** |
| Backend | `python3 -m pytest governance/dispatch -q` (per-dir) | **382 passed**, incl. `test_task_flows_claude_deepseek_hermes_paperclip_via_the_adapter` — the four-runtime e2e route still resolves correctly under today's paperclip/hermes default-on flags (#1789) |
| Composite | `bash scripts/check-pytest-suites.sh` (sanctioned per-suite runner) | **52/52 suites PASS**, no failures |
| Composite | `python3 scripts/check-feature-flags.py` | `feature-flags: OK` |

**Footnote on invocation artifacts:** a single bulk `python3 -m pytest gateway
governance -q` reports 33 failed / 858+2655 passed / 12 errors. This is NOT a
real regression — the repo has 15 sibling `governance/*/model.py` files and
duplicate `test_dispatch.py` basenames across `gateway/proxy` and
`gateway/sme-routing` without full package isolation, so pytest's bulk
rootdir collects the wrong module under the same import name
(`AttributeError: module 'model' has no attribute 'CODE_MANDATE_MISSING_DOC'`
is the same root cause). Every suite that "fails" in bulk mode passes clean
in isolation (see `governance/waves/tests/test_waves_ledger.py`: 15/15 passed
standalone). The repo's own sanctioned runner (`check-pytest-suites.sh`) runs
each suite in isolation for exactly this reason and is green. Nothing
currently warns a contributor that ad-hoc bulk `pytest <dir>` invocation is
unreliable here — tracked as backlog (P2) below, not filed as a child.

## Flag-state evidence (frontend compliance)

`infra/feature-flags/registry.yaml` (control-plane *service* registry) has
`default: on` for org_chart/skill_studio/task_board per the GR-5 reversal
policy (#1789, commit 03c16d3d): "a capability is either fully built and ON,
or not yet merged." But the file portal views actually gate on —
`portal/config/feature-flags.yaml`, read by `portal/server/config_flags.py`
(fail-closed) — still has `default: off` for all three. `check-feature-flags.py`
only cross-checks registry.yaml against `infra/terraform/variables.tf`, so it
does not catch this and prints OK while the views are dark. Filed as #1941 (P1).

`sessions` and `settings` surfaces correctly have no row in
`infra/feature-flags/registry.yaml` by design (portal views, not
control-plane services per `config_flags.py`'s own docstring) and sit
`default: off` at phase 11 in `portal/config/feature-flags.yaml` — expected
pre-promotion state, not a gap.

## Per-issue verification verdicts

| issue | area | verdict | evidence | severity | filed as |
|---|---|---|---|---|---|
| #1465 | infra/IaC | still real, unactioned | `infra/cloudbuild/live-triggers.json:12,20,28` — 3 triggers still ENABLED | P1 | #1942 |
| #132 | CMR | stale scaffold, no concrete pointer | boilerplate RCA-required body | P2 | backlog |
| #1529, #1637, #1733, #1734 | CMR/indexer | not verifiable | `vendor/CMR` submodule not initialized in this checkout | — | backlog (re-verify after `git submodule update --init vendor/CMR`) |
| #1537 | governance/PR-template | partially done | PR template already has the code-review-sme section (`.github/PULL_REQUEST_TEMPLATE.md:92-101`); branch-protection wiring depends on #1524, not locally verifiable | P2 | backlog |
| #1560, #1561, #1562, #1738 | SPOG/Hermes | design/decision work, not grep-verifiable against a fixed code pointer | — | P2 | backlog |
| #1890, #1891, #1892 | standards duplication | plausible, needs deeper multi-repo cross-check than this lane's scope | — | P2 | backlog |
| #1254, #1268, #1295, #1510, #1666, #1667 | epics | still open, still tracking live child work — no action needed from this review | — | — | n/a |
| #1790, #1795 | fleet ops, unlabeled | operational/bot-filed, not in this review's frontend/backend/middleware scope | — | — | not verified |
| #1894, #1895, #1896, #1900 | `[reconcile] suspect session` | automated reconcile-bot noise, not verified against code | — | — | not verified |

## Gaps filed

- **#1941** (P1) — org_chart/skill_studio/task_board dark despite GR-5 default-on policy
- **#1942** (P1) — 3 control-plane Cloud Build triggers still enabled (re-files #1465's unactioned fix)
- **#1935** (P1, cross-ref only, Parent: #1932, not a child of this epic) — governance/gateway pytest module-basename collisions cause false failures outside the sanctioned per-suite runner; filed independently by a concurrent lane covering the same root cause this review flagged as backlog above

## Backlog (not filed individually, capped at 10 children)

- #132, #1529, #1637, #1733, #1734, #1537, #1560, #1561, #1562, #1738, #1890, #1891, #1892 — see per-issue table above for why each was deferred rather than closed or re-filed.
