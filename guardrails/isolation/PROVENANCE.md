# guardrails/isolation — Provenance (AO-GR-10)

Lane-local record of assets consumed to build the tenant-isolation integrity
lane (issue `kushin77/agent-orchestrator#30`).  Every asset listed in the
issue body's "Cannibalize from" was **verified present** in the local clone,
consumed as a **PATTERN / REFERENCE**, and **adapted** (not copied) for a
multi-tenant SaaS control plane on the Python-stdlib stack.  Sources are
read-only clones under `/home/akushnir/agent-orchestrator/.research/`
(gitignored; never committed).  The repo-wide harvest index is
`docs/CANNIBALIZATION.md` (issue #8) — this file is the lane-local pointer
per AO-GR-10 and the issue's "verify each path, record provenance"
instruction.  All sources are internal `kushin77` fleet repositories; the
issue's paths were resolved to their actual locations where the repo layout
differs.

| Source repo (local path) | Asset (issue body → actual path) | Verdict | How it was adapted here |
|---|---|---|---|
| `kushin77/capital-underwriting` (`.research/fleet/capital-underwriting`) | `lib/accountIsolationRepair.ts` → `apps/server/src/lib/accountIsolationRepair.ts` (+ `test/security/accountIsolationRepair.test.ts`) | READY (shape) | Detect-first / report-only, opt-in repair (`REPAIR_MODE`), transactional all-or-nothing, idempotent re-runs, audited (repair ledger), defensive no-data-loss. → the isolation lane's detect → triage → opt-in `repair_execute` (validate-then-commit, audit ledger, never delete/guess) and the quarantine/rescue vocabulary. The source is TS/Prisma on a DB; this lane is a pure Python offline semantic core over a JSON tenant dataset. |
| `kushin77/saas-rbac` (`.research/fleet/saas-rbac`) | `src/rbac/README.md` → `services/backend-api/src/rbac/README.md` | REFERENCE | "Scope is a separate gate from permission" incident doctrine (#107 cross-tenant read): a route that bundles scope+permission can opt out of both; the scope guard compares the caller's tenant with the tenant in the path and never falls back cross-tenant. → the store-layer scope gate (`store.py`: `get`/`require`/`delete` are tenant-scoped, no cross-tenant fallback) and the R1–R3 scanner rules (a tenant-dimensioned index reached by its inner id is a #107-class finding). |
| `kushin77/CMR` (`.research/CMR`) | `docs/decision-records/ADR-0008` → `docs/decision-records/ADR-0008-agent-gate-triage.md` | REFERENCE | Agent-action gate triage: which gates generalize, severity/effort-based selection, a gate culture that is standalone + schema-validated. → the severity→BLOCK / SME-reviewer-queue / LOG triage gate (`triage.py`) and "a finding must be actionable on mechanism, not prose". |
| `kushin77/CMR` (`.research/CMR`) | `board/epics/EPIC-05-ai-guardrails.md` | REFERENCE | Enforcement layer doctrine (instruction layer + gates that genuinely check; AI output clears the same gates as human output; no auto-merge of failing work). → the fail-closed aggregation and the self-check as a regression gate. |
| `kushin77/CMR` (`.research/CMR`) | `guardrails/sweep/` (report + inventory) | REFERENCE | Sweep-style detection of anti-patterns across a codebase with evidence/reporting. → the deterministic text/JSON report renderers and the evidence-carrying finding model. |
| `kushin77/shared-governance` (`.research/fleet/shared-governance`) | `governance/multi-tenancy/*` (verified present: `tenant_manager.py`, `rbac_engine.py`, `quota_manager.py`, `audit_logger.py`, `policy_inheritance.py`, `README.md`) | PATTERN | Namespace-isolated tenant model (tenant lifecycle, scoped permission `scope: namespace/org/global`, audit trails). → the canonical dataset shape (tenant registry + per-tenant records + reserved fallback/quarantine buckets), the denormalized-`tenant_id` invariant, and the per-tenant cadence probe runner. |
| `kushin77/shared-governance` (`.research/fleet/shared-governance`) | `external-llm-egress-policy.md` → `GLOBAL_STANDARDS/external-llm-egress-policy.md` | REFERENCE | Egress/isolation policy culture (default-deny, explicit allow, audited). → default-deny cross-tenant access (store returns `None`/raises; probes fail closed). |

No assets were copied verbatim; identifiers, module structure and runtime
behavior were redesigned for this repo's pillar layout, doctrine
(`AGENTS.md`, `docs/GOLDEN-RULES.md`) and stack constraints (Python stdlib
only, fully offline).  No licenses were violated — all sources are internal
`kushin77` fleet repositories of this organization.
