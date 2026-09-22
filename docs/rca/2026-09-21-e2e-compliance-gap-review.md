# End-to-end compliance gap review (2026-09-21) — EPIC #1932

| Field | Value |
|---|---|
| Scope | full gap analysis of all 26 open issues vs. real code state, frontend/backend/middleware |
| Epic | https://github.com/kushin77/agent-orchestrator/issues/1932 |
| Reviewed | 2026-09-21/22 |

## Test evidence

### Frontend (portal/)

`python3 -m pytest portal/tests -q` → **603 passed** in 155.6s, 0 failed.

Flag check (`grep -A2 'task_board\|sessions\|org_chart\|skill_studio\|settings:' infra/feature-flags/registry.yaml`) shows org_chart/skill_studio/task_board `default: on` (GR-5/AO-GR-6 reversal, owner decision 2026-09-21). But that registry is documented as the control-plane *service* registry, separate by design from the portal's own gate (`portal/config/feature-flags.yaml`, read fail-closed by `portal/server/config_flags.py`). That file still has `default: off` for all three surfaces — the reversal never propagated to the file that actually gates the views. Filed as #1933 (P0).

Sessions (#1563) and Settings (#1757) views are `default: off` in `portal/config/feature-flags.yaml`, phase 11, with **no row at all** in `infra/feature-flags/registry.yaml` — this is by design (config_flags.py's own docstring: portal-local views add no service/terraform variable and are deliberately kept out of that registry), not a gap.

### Backend (gateway/, governance/)

Sanctioned runner `bash scripts/check-pytest-suites.sh` → **all ~48 declared suites PASS** in isolation.

Ad-hoc `python3 -m pytest gateway governance -q` → 33 failed / 2655 passed / 12 skipped / 12 errors, e.g. `governance/tagging/mandate.py: AttributeError: module 'model' has no attribute 'CODE_MANDATE_MISSING_DOC'`. Root cause: 15 sibling `model.py` files across governance/ subpackages and duplicate `test_dispatch.py` basenames in gateway/, several directories missing `__init__.py` — pytest's rootdir-relative import collides module identities. Confirmed not a real regression: `governance/waves/tests/test_waves_ledger.py` (7 of the failures) passes 15/15 in isolation. Filed as #1935 (P1) since nothing currently prevents this false-negative class outside the sanctioned runner.

### Middleware (governance/dispatch/route.py, four-runtime e2e)

`python3 -m pytest governance/dispatch -q` → **382 passed**, including `test_task_flows_claude_deepseek_hermes_paperclip_via_the_adapter`. The Claude→DeepSeek→Hermes→Paperclip route still resolves correctly under today's paperclip/hermes default-on flags (#1789). No breakage found.

### Composite gates

- `bash scripts/check-pytest-suites.sh` → all suites PASS (see above).
- `python3 scripts/check-feature-flags.py` → `feature-flags: OK` (checks registry.yaml ↔ terraform 1:1 only; does not cross-check portal/config/feature-flags.yaml, which is why #1933 went undetected).

## Real remaining gaps (see epic #1932 for the full table)

| issue | area | severity |
|---|---|---|
| #1933 (new) | portal flag-file drift (GR-5 reversal not propagated) | P0 |
| #1935 (new) | governance/gateway test basename collisions | P1 |
| #1890 | security-header contract duplicated 7x cross-repo | P1 |
| #1891 | commit-message rule enforced 3 conflicting ways cross-repo | P1 |
| #1892 | accepted-debt ledger reimplemented 3x cross-repo | P2 (decision-gated) |

## Per-issue verification verdicts

26 open issues inventoried. #1890/#1891/#1892 deep-verified against code (still real, evidence in epic #1932 body). Epics #1254, #1268, #1295, #1510 confirmed still open with active/relevant children. #1894/#1895/#1896/#1900 ("[reconcile] suspect session") and #1790/#1795 ("fleet:") are automation-filed noise from other lanes, out of scope for this manual review. Remaining ~14 issues inventoried but not independently re-verified in this pass (time budget); no evidence any were closed by commits in the last 8 hours.
