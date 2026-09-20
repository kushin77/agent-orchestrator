# Docs — agent-orchestrator

Index of the repo's canonical documentation. Agents start at
[`../AGENTS.md`](../AGENTS.md) (precedence-ordered doctrine).

## Canonical docs

| Doc | Purpose |
|-----|---------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Five-pillar control-plane architecture (source of truth; EPIC-00 = issue #4). |
| [`RELEASE-PLAN.md`](RELEASE-PLAN.md) | The v1.0.0 commitment and SemVer contract: surfaces under contract, v1.0.0 exit criteria mapped to issue #803's acceptance boxes, residual risks named (issue #1074). |
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
| [`SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md`](SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md) | Measured end-to-end (declared → implemented → gated → exercised) gap analysis of the system-app + app-model + governance path, with the four-way verdict per surface, the inert-guard list, the break-places and the provocations that earned each ENFORCED verdict (issue #1156). |
| [`CROSS-REPO-DEEPSEEK-ENHANCEMENTS.md`](CROSS-REPO-DEEPSEEK-ENHANCEMENTS.md) | DeepSeek and peer enhancements consumed/linked by the remote-control program (issue #549). |
| [`AGENTCONSOLE-HOSTING.md`](AGENTCONSOLE-HOSTING.md) | Where the browser operator console (AgentConsole) runs and who makes it run — the source/run two-repo handoff, flag-gated OFF; §11 carries the module/catalog packaging (issue #801, packaged by #813). |
| [`AGENTCONSOLE-GOLIVE.md`](AGENTCONSOLE-GOLIVE.md) | The repeatable AgentConsole go-live recipe (build -> transfer -> run -> secrets -> state -> cutover -> verify) with its rollback, and the gate that keeps the hosting contract from regressing (issue #1029, epic #607). |
| [`CANNIBALIZATION.md`](CANNIBALIZATION.md) | Harvest index — which harvested asset holds what, where the canonical copy of a duplicated asset belongs, and the per-lane provenance records (EPIC-00 phase 0, issue #8; the console entry is §17, issue #813). |
| [`FLEET-LEASE.md`](FLEET-LEASE.md) | fleet-cron D5 single-writer lease — `fleet/lease.py`'s `fcntl` (default) and KeyDB backends for the active-active pair, the RESP-over-stdlib-socket choice, and why `claims.py`/`channel.py` stay untouched (EPIC #706, issue #713). |
| [`FLEET-CRON-PARITY.md`](FLEET-CRON-PARITY.md) | fleet-cron D6 dual-run parity harness — `infra/fleet/parity.py` measures the container persona against the host persona over real dispatch ticks, with evidence at `.verify/fleet-parity.json` (EPIC #706, issue #714). |
| [`FLEET-PARITY.md`](FLEET-PARITY.md) | The lane-parity instrument — `scripts/fleet-parity/judge.py`'s ten dimensions and per-dimension planted negative controls, so a lane's acceptance verdict is a re-runnable measurement rather than logs an operator reads (issue #799). |
| [`FLEET-CUTOVER.md`](FLEET-CUTOVER.md) | fleet-cron D7 runbook — cutover, rollback, decommission, the freeze/enable/decommission lifecycle (EPIC #706, issue #715). |
| [`PR-QUEUE.md`](PR-QUEUE.md) | `scripts/pr-queue.sh` — the ordered serial squash-merge queue with an offline gate; codifies the 2026-09-17 by-hand queue-clearing (issue #1053, EPIC #878). |
| [`PR-RUNNER.md`](PR-RUNNER.md) | `fleet/runner/` — the scheduled shared-services PR runner: per-(pr, sha) ranked evidence, held scratch worktrees, `ao/gate-of-record` posts, merges through the merged-tree seam and `scripts/merge-pr.sh`; ten measured lessons as named controls (issue #1343, parent #1295). |
| [`GIT-ENV-VARIABLES.md`](GIT-ENV-VARIABLES.md) | Canonical registry of the session env contract exported by governance/isolation/** and named in AGENTS.md golden rule 15 (issue #608 → EPIC #616). |
| [`PYTHON-PATTERNS.md`](PYTHON-PATTERNS.md) | The Python-scoped pattern canon — `PP-1` is the #506 date bomb (a fixture that pins a seed but not the evaluation), with the measured refusal that says why its enforcement is behavioural and lives in `scripts/check-chat-finops.sh` (issue #1028). |
| [`INFRA-LIMITS.md`](INFRA-LIMITS.md) | The sandbox + ephemeral-storage contract — the blocked network, the read-only-except-workspace filesystem, the shared `/tmp` tmpfs, the write-is-not-a-write-until-read-back rule, and the `scripts/check-infra-limits.sh` guard that enforces them (EPIC #708, issue #729). |
| [`BOARD-METADATA-AUDIT.md`](BOARD-METADATA-AUDIT.md) | The live board-metadata pass — every open issue's `class/type/priority/area` (+`gdc`/`pillar`), milestone, `epic:<slug>` and declared chain edges, with the before/after coverage and the consumer transcripts (issue #1158). |
| [`TAGGING.md`](TAGGING.md) | The tag authority end to end — one declared vocabulary per tag dimension (borrowing, never re-declaring, the `class` ladder, the FinOps tiers and the fleet roles), the new `posture` (`overall`/`saas`/`iac`/`no-human-needed`/`human-gated`) and `lifecycle` (SDLC stage) dimensions, the tag → gate derivation by channel (pr/ci/cd/ops), the generated matrix, and the gate whose negative control provokes all eleven refusals by name (issue #1175). |
| [`../control-plane/cockpit/README.md`](../control-plane/cockpit/README.md) | The terminal cockpit (AgentConsole) — the operator client of the RC-3 control API and the authenticated SSE streams; keyboard-first, role-tiered, drillable, flag-gated OFF (EPIC #551, issue #566). |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Human contributor workflow. |
| [`../RELEASING.md`](../RELEASING.md) | SemVer release process. |

## Fleet, governance and FinOps docs

Indexed by issue #1206: these 31 docs were previously carried as an exemption
baseline in `scripts/check-docs.sh` (the `idx_quarantine` list, held open under
issue #629). The baseline is now empty, so every tracked `docs/**/*.md` is
reachable from this index.

| Doc | Purpose |
|-----|---------|
| [`GOLDEN-RULES.md`](GOLDEN-RULES.md) | The canonical product spine (AO-GR-1..AO-GR-28) that `AGENTS.md` and the root ratification pointer defer to. |
| [`AGENT-IDENTITY.md`](AGENT-IDENTITY.md) | One shared agent-identity schema, owner lane `registry` (issue #346, parent #338). |
| [`MODULE-REGISTRY.md`](MODULE-REGISTRY.md) | Module registry — the measured module inventory, pointing at the contract in `governance/modules/README.md` (issue #445). |
| [`MODULE-BRIEF.md`](MODULE-BRIEF.md) | Module brief — what every repo must carry, at which pin, and whether it is current (issue #447, ADR-0012). |
| [`CONTROL-COVERAGE.md`](CONTROL-COVERAGE.md) | Control coverage — the enterprise spine (issue #874). |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | Observability — the monitoring boundary for agent-orchestrator; declared and gated (issue #496, EPIC #494). |
| [`QA-GATE.md`](QA-GATE.md) | QA gate stack — `make gate`, qa-loop and merge gate (issue #29). |
| [`SURFACE-CLASS.md`](SURFACE-CLASS.md) | Per-surface target solution-classes — the CMR quality rung each product surface declares. |
| [`MECHANICAL-EXECUTION-LAYER.md`](MECHANICAL-EXECUTION-LAYER.md) | Mechanical execution layer — concept & intent (intent-only spec, issue #239). |
| [`BOARD-ATTACK-PLAN.md`](BOARD-ATTACK-PLAN.md) | Board attack plan (milestone → epic → class) — the PMO coordination artifact; no product code. |
| [`FLEET-STATE.md`](FLEET-STATE.md) | Unified fleet-state projection across the five stores one work item touches (issue #323). |
| [`FLEET-CAPABILITY-DRIFT.md`](FLEET-CAPABILITY-DRIFT.md) | Capability drift — restarting a rung that does not implement what the repository declares (issue #319). |
| [`FLEET-DASHBOARD-GAP-ANALYSIS.md`](FLEET-DASHBOARD-GAP-ANALYSIS.md) | Fleet dashboard gap analysis — terminal TUI vs web single-pane-of-glass (issue #330). |
| [`LEASE-POLICY.md`](LEASE-POLICY.md) | One declared policy for every fleet lease and TTL (issue #322). |
| [`LEASE-HOOK.md`](LEASE-HOOK.md) | The opt-in `pre-commit` file-lease hook: what it refuses, the situations it fails open on, the opt-in install, and the two documented opt-out paths (issue #1541). |
| [`SESSION-FLEET-SYNC.md`](SESSION-FLEET-SYNC.md) | Session fleet sync — the sync contract and gap register (issue #181). |
| [`CROSS-REPO-LESSONS-SYNC.md`](CROSS-REPO-LESSONS-SYNC.md) | The declared relationship between the two lessons loops either side of the repo boundary. |
| [`SHARED-SERVICES-FALLBACK.md`](SHARED-SERVICES-FALLBACK.md) | Shared-services fallback rung — frozen contract, owner lane `gateway/health` (issue #375). |
| [`LIVE-DATA-BRIDGE.md`](LIVE-DATA-BRIDGE.md) | Live data bridge (`ao.bridge/v1`) — the versioned read transport over the four state families. |
| [`GLOSSARY.md`](GLOSSARY.md) | Glossary — the fleet's role vocabulary (normative pointer, issue #777). |
| [`SHELL-PATTERNS.md`](SHELL-PATTERNS.md) | Shell patterns — the shapes this repository refuses (EPIC #616, issue #621). |
| [`SCRATCH-SPACE-DISCIPLINE.md`](SCRATCH-SPACE-DISCIPLINE.md) | Scratch-space discipline — keeping an agent's scratch from taking the machine (issue #488). |
| [`OPERATOR-ACCESS.md`](OPERATOR-ACCESS.md) | Principal access — every way into the fleet, and what each one needs (runbook, issue #763). |
| [`PORTAL-OFFLINE-DEV.md`](PORTAL-OFFLINE-DEV.md) | Portal offline dev run — the fleet SPoG with zero external infra (dev stopgap, issue #732). |
| [`EDGE-CUTOVER.md`](EDGE-CUTOVER.md) | Edge cutover — how `ai.purebliss.app` is fronted and what is retired; a declaration, not a deployment (issue #731). |
| [`CHAT-MOUNT.md`](CHAT-MOUNT.md) | The chat mount contract — how the conversational surface appears in the OS shell (issue #511, EPIC #500). |
| [`CODEIDX-CAPABILITY-REGISTER.md`](CODEIDX-CAPABILITY-REGISTER.md) | What the fleet needs from `kushin77/code-indexing`, per capability, and how we know we have it. |
| [`DIAGRAMS-CAPABILITY-REGISTER.md`](DIAGRAMS-CAPABILITY-REGISTER.md) | What "fully capable" means for the fleet's diagrams surface (issue #467). |
| [`erp-finops/compliance-audit.md`](erp-finops/compliance-audit.md) | ERP/FinOps compliance audit — phase-4 validation & governance (issue #676, EPIC #665). |
| [`erp-finops/current-state.md`](erp-finops/current-state.md) | Current-state money map — subscription flows and the manual reconciliation points (PF-1, issue #666, EPIC #665). |
| [`erp-finops/saas-metrics-current-state.md`](erp-finops/saas-metrics-current-state.md) | SaaS metrics current state — MRR/ARR, cloud compute burn, invoicing bottlenecks and the silo map (issue #668). |
| [`erp-finops/token-baseline.md`](erp-finops/token-baseline.md) | DeepSeek token-flow baseline (issue #667, EPIC #665). |

## Planned (later issues)

- `adr/` — architecture decision records (issue #7).
