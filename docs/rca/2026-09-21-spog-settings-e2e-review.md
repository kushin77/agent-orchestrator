# SPOG settings surface end-to-end gap review (epic #1667)

## Scope

Frontend/backend/middleware gap review of the Single Pane of Glass console's
settings surface, against `origin/master`, 2026-09-21. Read-only review — no
implementation in this lane except where noted. Settings are declared config
(IaC/GR-5): the gap is that no view lets an operator *see* current state in
one place, not that values should become click-to-mutate.

## Standard, not bespoke (binding constraint)

Settings must land as **one shared component/pattern every view reuses**, at
the same tier as the `CP.el` DOM helpers and the `NAV_GLOBAL`/`NAV_TENANT`
registration array (`portal/static/js/console.js`) that Task Board, Sessions,
and Org Chart already share — not a bespoke page that hand-reads each config
file. Concretely:

- The nav entry registers through `NAV_GLOBAL` in `console.js`, exactly like
  `sessions`/`orgchart`/`skillstudio` — no parallel routing mechanism.
- The middleware aggregator (`portal/server/settings.py`, gap G3 below) must
  expose one **standard row schema** — `domain`, `key`, `value`,
  `source_file`, `editable: false` — that any settings-consuming view calls
  once. Per-domain readers are not acceptable as the shipped shape.
- Any settings-adjacent code already in the repo that reads a config file
  ad hoc for its own view (none found doing this yet — see gap table) is a
  migration target onto the shared module, filed as follow-up, not fixed in
  place.

## Current-state table

| Settings domain | File | View | Backend reader | Middleware aggregator |
|---|---|---|---|---|
| Settings view (any domain) | — | **absent** — no `settings.html`, no `NAV_GLOBAL`/`NAV_TENANT` entry in `portal/static/js/console.js` | — | absent |
| Portal surface flags | `portal/config/feature-flags.yaml` | absent (values only referenced in per-view empty-state hint text, e.g. `portal/static/views/sessions.html:43`) | present — `portal/server/config_flags.py` (`surface_enabled`) | absent |
| Fleet job schedule | `config/fleet-jobs.json` | absent | absent — no reader in `portal/server/*.py` | absent |
| Dispatch tier policy | `governance/dispatch/tier-policy.json` | absent | present — `governance/dispatch/tiered.py` (dispatch-time only, not exposed to portal) | absent |
| Gate skip-budget | `scripts/skip-budget.json` | absent | absent — no `portal/server` reader | absent |
| `enable_paperclip`/`enable_hermes` | `infra/feature-flags/registry.yaml` + `gateway/providers/flags.py` | absent | present (gateway-internal only) | absent |
| Nous secret declaration (#1748) | `.fleet/` / secret manifest (per #1748) | absent | present (fleet-internal only) | absent |
| CTO/PMO module config | `integrations/*/module.yaml` pattern | absent | per-module only | absent |
| Identity/RBAC presets | `identity/` | absent | per-module only | absent |

Every declared-config domain above is edited by hand + PR today, which is the
correct IaC pattern (GR-5) — none of these should gain a mutate path. The gap
is uniformly "no read-only projection," never "no edit UI."

## Cannibalization sources (GR-10 provenance)

- **Source:** `~/capital-underwriting` (sibling repo on this box, same owner
  `kushin77`, proprietary/internal-use license per its `LICENSE` file — no
  external license to carry forward, but per-repo reuse still requires an
  explicit provenance citation).
- **Path:** `apps/frontend/vibe/src/components/views/SettingsView.tsx` (531
  lines) plus its `settings/` subdirectory (`IntegrationsSettings.tsx`,
  `CustomFieldsSettings.tsx`, `DomainsSettings.tsx`, `BrandingSettings.tsx`,
  `AdvancedSettings.tsx`, `VerticalsSettings.tsx`) and
  `test/contract/settingsContract.test.ts`.
- **What it solves:** a domain-partitioned settings view (one section per
  config domain, each a card) plus a settings contract test shape — the same
  decomposition this review's standard schema (`domain`, `key`, `value`,
  `source_file`, `editable`) would render into cards.
- **Reuse plan (for the implementation issue, not this lane):** port the
  section/card layout and the contract-test pattern, not the React/Vibe
  component code itself (this portal is vanilla JS + `CP.el`, not
  React) — cite this doc + `capital-underwriting@<path above>` in that PR's
  provenance line.
- No settings-aggregation pattern found in `~/leaderboard` or
  `~/shared-services` (looked only, per instruction; neither repo has a
  `settings.py`/`SettingsView` hit).

## Gap list

1. **G1 — P0 — no Settings view exists at all.** `portal/static/js/console.js`
   `NAV_GLOBAL`/`NAV_TENANT` (lines ~30-65) has no `settings` entry and
   `portal/static/views/` has no `settings.html`. Filed **#1757**. Smallest fix: one
   `settings.html` view + one `NAV_GLOBAL` entry, rendering rows from G3's
   aggregator via `CP.el`, following the `sessions`/`orgchart` wiring exactly.
2. **G2 — P0 — no middleware aggregator.** No `portal/server/settings.py`
   exists; no module joins the declared-config domains into one read-only
   snapshot the way `portal/server/sessions.py` joins `.fleet/`/`.board/`/
   `.deepseek-agent/`. Filed **#1756**. This is the core middleware gap and blocks G1.
3. **G3 — P1 — `config/fleet-jobs.json` has no reader anywhere in
   `portal/server/*.py`.** Confirmed via `grep -rln "fleet-jobs.json"
   portal/server/*.py` (no hits). Filed **#1758**. Smallest fix: a reader function in the new
   `settings.py` aggregator (G2), fail-closed like `config_flags.py`.
4. **G4 — P1 — `scripts/skip-budget.json` has no reader anywhere in
   `portal/server/*.py`.** Filed **#1759**. Same evidence method as G3, no hits. Same fix
   path via G2.
5. **G5 — P2 — declared-but-unsurfaced domains without any portal-facing
   reader:** dispatch tier policy (`governance/dispatch/tier-policy.json`,
   reader exists only in `governance/dispatch/tiered.py`, dispatch-internal),
   `enable_paperclip`/`enable_hermes` (reader exists only in
   `gateway/providers/flags.py`, gateway-internal), Nous secret declaration
   (#1748, fleet-internal reader only). Filed **#1760**. Each needs a thin projection function
   registered in G2's aggregator rather than a bespoke portal reader.
6. **G6 — P2 — migrate any future per-view config reads onto the shared
   module.** No existing per-view ad hoc config read was found in this
   review (per-domain readers all live in their owning subsystem, not in a
   portal view), so there is nothing to migrate today — this item is a
   standing constraint for review, not a current violation: any settings-
   adjacent code added to a view going forward must call the G2 aggregator
   instead of reading its own file.

## Roadmap (dependency order)

1. G2 (middleware aggregator, standard row schema) — blocks everything else.
2. G1 (Settings view + `NAV_GLOBAL` entry, consumes G2) — file-disjoint from G2
   (`portal/server/settings.py` vs `portal/static/views/settings.html` +
   `portal/static/js/console.js`).
3. G3, G4 (fleet-jobs / skip-budget readers) — additive functions inside G2's
   module once it exists; can land in parallel with each other, after G2.
4. G5 (tier-policy / paperclip-hermes / Nous projections) — additive to G2,
   parallel with G3/G4, no shared files between the three.
5. G6 — standing review constraint, no issue filed (nothing to migrate today).

## Lanes (file-disjoint)

- Lane A: `portal/server/settings.py` (G2).
- Lane B: `portal/static/views/settings.html` + `portal/static/js/console.js`
  `NAV_GLOBAL` entry (G1, depends on Lane A's schema but not its code).
- Lane C: additive reader functions inside `portal/server/settings.py` for
  G3/G4/G5 — sequenced after Lane A merges (same file), not parallel with it.
