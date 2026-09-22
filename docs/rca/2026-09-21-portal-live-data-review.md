# RCA: portal "no enhancements visible, still fake data" (2026-09-21)

Parent: #1667

## Complaint (verbatim)

"we've made many pull requests over the past 24-48 hours and I have not
seen any enhancements to our product portal, there is no real live data,
it's still showing fake data."

## Verdict up front

Both premises are wrong, but for understandable reasons. The PRs are real
and merged. The data sources are real (no fake/mock code runs in the
serving path — see below). What's actually true: (1) every one of these
surfaces is flag-gated OFF by design (GR-5 — a new surface ships dark
until a reviewed go-live), and (2) nobody has ever started the portal dev
server this session, or via any `make` target, so there was never a
running instance to look at in the first place. "I haven't seen anything"
is explained by never having loaded a URL where anything would render —
not by the work being fake or missing.

> **Amended 2026-09-22:** the flag-gated-OFF-by-design / GR-5 default cited
> above and below ("flags are correctly off by design") was reversed by
> policy-gr5-enabled-by-default (2026-09-21, AO-GR-6); see
> docs/GOLDEN-RULES.md#ao-gr-6--flag-gated-off-by-default.

## Per-PR flag state

| PR | Feature | `services.<flag>` | `default` | `promoted` |
|---|---|---|---|---|
| #1635 | Task Board + fleet board | `task_board` / `fleet_projection` | off | false |
| #1606 | Org Chart + Skill Studio | `org_chart`, `skill_studio` | off | false |
| #1720/#1735 | Sessions view + operator controls | `operator_terminal` (AgentConsole) | off | false |
| #1761 | portal suite fix (flag defaults corrected) | n/a — wiring fix only | — | — |

Top-level `services.portal` itself is also `default: off, promoted: false,
posture: hold`. All five merged commits verified present on
`origin/master` (`4af62f4f`, `3836e015`, `04f68bf9`, `f466b861`,
`0b2dfa29`). None of this is a bug: `portal/server/config_flags.py` reads
`portal/config/feature-flags.yaml` fail-closed, so an unpromoted view is
absent (404 `feature_disabled`), never half-rendered fake data.

## Live-data-source per view

| View | Declared root | Exists on this box | Flag state | Verdict |
|---|---|---|---|---|
| Task Board | `.board/*` (claims.jsonl, snapshot.json, focus.json) | yes | off | real-but-flag-off |
| Fleet board | `.board/snapshot.json` | yes | off | real-but-flag-off |
| Sessions | `.fleet/`, `.board/`, `~/.deepseek-agent` | `.fleet` absent; `.board` and `~/.deepseek-agent` present | off | real-but-root-absent (partial) — `.fleet` never existed this session |
| Org Chart | `registry/personas/org-chart.yaml` + cards | present | off | real-but-flag-off |
| Skill Studio | workbook-9 skill API | present | off | real-but-flag-off |

## Fake/mock grep sweep

`grep -rln "mock|fake|fixture|stub|hardcoded|TODO.*real|placeholder"` over
`portal/server/*.py`, `portal/static/js/*.js`, `portal/static/views/*.html`
returns only comments that explicitly *disclaim* fake data (e.g.
`livestore.py:7: "telemetry — never hardcoded demo rows (issue #348)"`,
`state.py:331: "never a hardcoded series"`). No mock/fixture code runs in
the actual serving path. Premise "still showing fake data" does not hold
— there is nothing being shown at all, because nothing is running.

## Running-instance finding

No `make run-portal`, `make portal-demo`, or equivalent target exists in
the `Makefile`. `scripts/portal-dev-session.py` exists and can start a dev
server manually, but grep + this session's own history confirm it has
never been invoked this session or by any recent automation. There is no
deploy path either — `enable_portal`/`enable_web` are terraform-gated off.
Conclusion: nobody has ever pointed a browser at a running instance of
this checkout's portal. That is sufficient by itself to explain "I have
not seen any enhancements."

## Tenant-scoping finding

`grep -rln tenant portal/server/` hits 20 files (`erp.py`, `task_board.py`,
`ops_health.py`, `finops.py`, `authz.py`, etc.) — tenant-scoped read paths
exist throughout. No file suggests cross-tenant/cross-org leakage; the
"remove all other tenants except our own" framing in the assignment
doesn't map to any concrete gap found in the code — flagging this
explicitly rather than guessing at a fix.

## The one thing most likely to fix "I haven't seen anything"

File one issue proposing a `make portal-demo` target: starts
`scripts/portal-dev-session.py` with a documented demo flag-set (task
board, org chart, skill studio, sessions/operator-terminal) forced ON via
an env override, never touching `infra/feature-flags/registry.yaml`
defaults or terraform. This lets the owner actually load the URL and see
the four merged PRs render against real `.board`/`registry/personas` data,
without changing GR-5 production posture.

## Issues filed

- #1771 — `make portal-demo` demo-mode target
  proposal, `class:enterprise type:task priority:P1 area:standards
  gdc:enterprise`, assignee kushin77.

No other real gaps found (data sources are real, flags are correctly off
by design) — filing a second issue for "flags are off" would just
re-litigate intended GR-5 behavior, so only the demo-target issue is
filed.
