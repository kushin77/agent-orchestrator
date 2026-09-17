# Docs — agent-orchestrator

Index of the repo's canonical documentation. Agents start at
[`../AGENTS.md`](../AGENTS.md) (precedence-ordered doctrine).

## Canonical docs

| Doc | Purpose |
|-----|---------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Five-pillar control-plane architecture (source of truth; EPIC-00 = issue #4). |
| [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md) | One-issue-one-lane parallel dispatch contract, phase/wave sequencing 0–8. |
| [`PAPERCLIP-ING-GAP-ANALYSIS.md`](PAPERCLIP-ING-GAP-ANALYSIS.md) | Sourced fork-map of upstream `paperclip.ing` against the fleet's own primitives (issue #368). |
| [`ERP-MODULE-GAP-ANALYSIS.md`](ERP-MODULE-GAP-ANALYSIS.md) | Feature-by-feature gap of frappe/erpnext against our pillars — the indexer-fed ERP module plan (issue #612, EPIC #645). |
| [`GIT-TEMPLATES-GAP-ANALYSIS.md`](GIT-TEMPLATES-GAP-ANALYSIS.md) | Git-ecosystem gap analysis — class/pattern/template/env/governance/clobber/RCA/orphan-checker, each mapped to a remediation lane (issue #608 → EPIC #616). |
| [`GOVERNANCE.md`](GOVERNANCE.md) | Branch, provenance, session-label, review/merge conventions. |
| [`PAPERCLIP-ING-INTEGRATION.md`](PAPERCLIP-ING-INTEGRATION.md) | Frozen fleet ↔ upstream Paperclip integration seam — heartbeat/ticket/budget contracts (ADR-0013, issue #370). |
| [`PAPERCLIP-ING-DEPLOY.md`](PAPERCLIP-ING-DEPLOY.md) | The self-hosted paperclip runtime runbook — declaration-only, flag-gated OFF, pinned upstream release, offline `/api/health` probe (issue #411). |
| [`ENTERPRISE-WORKBOOK-GAP-ANALYSIS.md`](ENTERPRISE-WORKBOOK-GAP-ANALYSIS.md) | Paperclip enterprise-workbook four-pillar gap analysis — measured master mapping, C-suite org-chart → persona mapping, and the delta epic (EPIC #631, children #632–#644) (issue #614). |
| [`CROSS-REFERENCE-SPINE.md`](CROSS-REFERENCE-SPINE.md) | Typed relationship edges (`cmr-refs:` markers, closed vocabulary, RCA nodes) — EPIC #138, issue #384. |
| [`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) | The cross-repo boundary contract — a repo remediates findings for itself only; foreign work is handed over by direction issue (NG4, issue #125). |
| [`CTO-OVERLAY.md`](CTO-OVERLAY.md) | The per-repo drop-in governance overlay — four real layer gates, BLOCKING/WARNING, preserved no-false-green tally (EPIC #144, issue #147). |
| [`AUTHORITY-MODEL.md`](AUTHORITY-MODEL.md) | Scoped admin rights, repo separation, schema-enforced separation of duties, end-to-end closure (EPIC #144, issue #150). |
| [`ENTERPRISE-ROLLUP.md`](ENTERPRISE-ROLLUP.md) | Tenant hierarchy + org aggregate view — a projection over per-repo fleets, never a second source of truth (EPIC #144, issue #151). |
| [`SESSION-FLEET-LESSONS.md`](SESSION-FLEET-LESSONS.md) | Session-fleet lessons register: six lessons from first live steering + seven-item enterprise hardening register, each item mapped to its owning issue (issue #180). |
| [`FLEET-TEMPLATE.md`](FLEET-TEMPLATE.md) | The parameterized per-repo fleet template — schema-validated composition, definition-vs-run-state split, drift + two-repo isolation (EPIC #144, issue #146). |
| [`SME-ROUTING.md`](SME-ROUTING.md) | SME-squad routing + capability/route/tier FinOps: domain→SME, complexity→chain+tier, caps and the human/advisor escalation terminal (EPIC #144, issue #149). |
| [`REGISTRY-PROVENANCE.md`](REGISTRY-PROVENANCE.md) | Harvest provenance for the profile/persona/SME vocabularies, and the frozen canonical-CMR parity baseline with its refresh command (issue #145). |
| [`MODULE-ADMISSION.md`](MODULE-ADMISSION.md) | The parent-side sub-module admission contract — what a sub-module is, what each side declares, and what a module must not inherit (EPIC #422, issue #423). |
| [`CROSS-REPO-SYNC-OWNER.md`](CROSS-REPO-SYNC-OWNER.md) | The standing peer-board triage: dispositions with mandatory provenance, dry-run direction issues, no silent adoption or cross-repo close (EPIC #422, issue #427). |
| [`REMOTE-CONTROL-GAP-ANALYSIS.md`](REMOTE-CONTROL-GAP-ANALYSIS.md) | Inventoried control-surface gap: every control verb is local; paperclip is the control plane and tmux is a back door, not the mechanism (issue #550). |
| [`CROSS-REPO-DEEPSEEK-ENHANCEMENTS.md`](CROSS-REPO-DEEPSEEK-ENHANCEMENTS.md) | DeepSeek and peer enhancements consumed/linked by the remote-control program (issue #549). |
| [`AGENTCONSOLE-HOSTING.md`](AGENTCONSOLE-HOSTING.md) | Where the browser operator console (AgentConsole) runs and who makes it run — the source/run two-repo handoff, flag-gated OFF; §11 carries the module/catalog packaging (issue #801, packaged by #813). |
| [`AGENTCONSOLE-GOLIVE.md`](AGENTCONSOLE-GOLIVE.md) | The repeatable AgentConsole go-live recipe (build -> transfer -> run -> secrets -> state -> cutover -> verify) with its rollback, and the gate that keeps the hosting contract from regressing (issue #1029, epic #607). |
| [`CANNIBALIZATION.md`](CANNIBALIZATION.md) | Harvest index — which harvested asset holds what, where the canonical copy of a duplicated asset belongs, and the per-lane provenance records (EPIC-00 phase 0, issue #8; the console entry is §17, issue #813). |
| [`FLEET-CUTOVER.md`](FLEET-CUTOVER.md) | fleet-cron D7 runbook — cutover, rollback, decommission, the freeze/enable/decommission lifecycle (EPIC #706, issue #715). |
| [`GIT-ENV-VARIABLES.md`](GIT-ENV-VARIABLES.md) | Canonical registry of the session env contract exported by governance/isolation/** and named in AGENTS.md golden rule 15 (issue #608 → EPIC #616). |
| [`PYTHON-PATTERNS.md`](PYTHON-PATTERNS.md) | The Python-scoped pattern canon — `PP-1` is the #506 date bomb (a fixture that pins a seed but not the evaluation), with the measured refusal that says why its enforcement is behavioural and lives in `scripts/check-chat-finops.sh` (issue #1028). |
| [`../control-plane/cockpit/README.md`](../control-plane/cockpit/README.md) | The terminal cockpit (AgentConsole) — the operator client of the RC-3 control API and the authenticated SSE streams; keyboard-first, role-tiered, drillable, flag-gated OFF (EPIC #551, issue #566). |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Human contributor workflow. |
| [`../RELEASING.md`](../RELEASING.md) | SemVer release process. |

## Planned (later issues)

- `adr/` — architecture decision records (issue #7).
