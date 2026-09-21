# CTO-OFFICE — agent-orchestrator's job as the Chief Technology Office for the CMR

Issue: #1650 (Parent: #1510). See also `docs/ARCHITECTURE-2026-09.md` for the
control chain this office sits above.

## What the CMR is, and where this repo sits

`kushin77/CMR` is the **hub**; per-tenant repos (this one, plus others named in
`gdc-manifest.yaml`) are **spokes**, each pinning a read-only copy of hub
modules under `vendor/CMR/catalog/modules/*` (never edited locally — GR-5,
this repo's `CLAUDE.md` "Never" list). `agent-orchestrator` is itself both: a
**spoke** that consumes hub doctrine (golden rules, `PROGRAM-MANAGEMENT.md`,
`MODEL-PROFILES.md` — all quoted from `vendor/CMR/`, none copied locally, per
`AGENTS.md` precedence order and `governance/pmo/README.md`'s own note that
`vendor/CMR/docs/PROGRAM-MANAGEMENT.md` is "unpopulated in a fresh worktree, so
quoted rather than linked"), and the **product** that IS the control plane
(`AGENTS.md`: "This repository IS the product... not a hub, and not a spoke's
application").

## Authority — root/super-admin, technical scope only

**Status: on branch `issue-purebliss-single-tenant-org` (PR #1580), not yet
merged to `master`.** Everything in this section is DECLARED-ONLY as of
2026-09-20 — cited from that branch, not from `master`.

`identity/rbac/presets/cto-superadmin.yaml` (issue #1573, Parent: #1510)
extends — never replaces — the baseline `cto` role in
`identity/rbac/presets/csuite.yaml` (merged, on `master`). It adds exactly two
named permissions (`audit:read`, `roles:read`) plus a documented, time-boxed
break-glass path. Per this repo's own doctrine (`identity/rbac/model.py`
WILDCARD rule, cited in the preset's own header comment), the wildcard
permission `*:*` is reserved to the Owner preset only and is **never** granted
here, even to "root admin" — GR-5/GR-6.

`registry/personas/offices/cto/charter.yaml` (same branch) declares the scope:

- **`scopeOfAuthority.domains`:** architecture-decisions,
  platform-code-authorship-oversight, iac-review-and-apply-gate,
  security-posture, delivery-completion.
- **`mayApprove`:** pull-request-merge-to-master,
  architecture-decision-record-acceptance, terraform-apply-authorization
  (still executed via the governed IaC pipeline, never ad hoc — GR-5),
  security-exception-with-expiry (time-boxed, logged, never silent).
- **`mayNotApprove`:** org-or-tenant-administration (CEO only, `roles:manage`),
  budget-cap-changes (CFO only, `budget:manage`), and its own unreviewed work
  (separation-of-duties — a CTO-authored change still needs an independent
  reviewer/auditor persona, never self-attested).
- **`reportsTo`: ceo.**

This is the root/super-admin **technical** authority the owner directive
names — explicitly bounded, explicitly not org/budget authority, and not yet
live on `master`.

## What it governs (what exists today, on `master`)

- **Module onboarding / admission:** `docs/MODULE-ADMISSION.md` (EPIC #422,
  issue #423) — the parent-side sub-module admission contract; what a spoke
  declares, what it must not inherit from the hub.
- **Gates:** `scripts/verify.sh` (`make verify`, GR-12) is the composite gate
  every change passes through before landing; `docs/CTO-OVERLAY.md` is the
  per-repo drop-in governance overlay (four layer gates, BLOCKING/WARNING,
  EPIC #144, issue #147) a spoke installs to inherit the office's posture.
- **Tiers / FinOps:** each AgentProfile seed declares its own
  `defaultModelTier` (`registry/profiles/seeds/*.yaml`); the canonical tier
  ladder document is hub-owned (`kushin77/CMR docs/MODEL-PROFILES.md` — no
  local copy exists in this repo, confirmed by `find . -iname
  MODEL-PROFILES.md` excluding `vendor/`).
- **IaC mandate (GR-5/GR-17):** no ad-hoc `terraform apply` (GR-5, this repo's
  `CLAUDE.md` "Never" list); local-code-first debugging against this checkout
  (GR-17).
- **Dispatch:** `fleet/brain.py` + `governance/dispatch/` — the sole top-level
  orchestrator (ADR-0033; see `docs/ARCHITECTURE-2026-09.md` Layer 2).

## How a spoke gets work from the office and reports back

A spoke's unit of work is tracked as a **paperclip ticket** — the single join
node per [ADR-0014](decision-records/ADR-0014-ticket-single-join-node-contract-v2.md)
(a projection, never itself authority). The flow, measured against
`integrations/paperclip/`:

1. Work is opened as a GitHub issue (GR-2, issue-first) with `Parent:` pointing
   at the owning epic.
2. `integrations/paperclip/mapping.py` maps the board snapshot + claims to a
   ticket; `integrations/paperclip/reporting/composer.py` composes the
   per-module brief a spoke owes the office (`capability.py` declares the
   contract: what tools it needs, what sources it reads, where the frozen
   artifact lives).
3. Status flows back through `governance/pmo/` read-only views (`deps`,
   `lanes`, `report`, `raid`, `aging`, `gates`) — the office (or any operator)
   queries the ticket graph without a second source of truth.
4. Landing is gated (`make verify`, `make land`, `make master-attestation` —
   see `docs/ARCHITECTURE-2026-09.md` Layer 5) before a spoke's work counts as
   done.

## Escalation to CEO / board

Per `registry/personas/offices/cto/charter.yaml`'s `escalation.to: ceo`
(branch `issue-purebliss-single-tenant-org`, not yet merged): the CTO office
escalates upward when a decision exceeds its technical scope (org/tenant
administration, budget caps) or when a security exception needs sign-off
beyond a time-boxed exception. No board-level escalation path exists in this
repo beyond `reportsTo: ceo` — a repo-local board/CEO relationship is out of
this repo's scope by design (this repo governs the technical control plane,
not org governance).

## Golden-rule citations (by number, `AGENTS.md`)

- **GR-2** (issue-first): every change tracked in a GitHub issue before work starts.
- **GR-3** (one issue = one lane = one branch): no two lanes share a file.
- **GR-5** (IaC / vendor): no ad-hoc `terraform apply`; `vendor/` is pinned and
  never edited.
- **GR-6** (no wildcard authority / no secrets in code): the CTO-superadmin
  preset explicitly never grants `*:*`.
- **GR-12** (verification before completion): `make verify` is the gate of record.
- **GR-17** (local-code-first): debug against this checkout first.

## Evidence commands run for this document

- `git show origin/issue-purebliss-single-tenant-org:identity/rbac/presets/cto-superadmin.yaml`
- `git show origin/issue-purebliss-single-tenant-org:registry/personas/offices/cto/charter.yaml`
- `git ls-tree -r --name-only origin/issue-purebliss-single-tenant-org | grep -i "cto-superadmin\|registry/personas/offices/cto"`
- `find . -iname MODEL-PROFILES.md` (excl. `vendor/`) — empty
