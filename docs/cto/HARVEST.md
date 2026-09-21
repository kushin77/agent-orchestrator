# CTO Office — Harvest (issue #1573, Parent: #1510)

GR-10 provenance record for the CTO office module. Mined read-only from
`/home/akushnir/leaderboard` and `/home/akushnir/capital-underwriting`
(vendor/leaderboard mirror inside capital-underwriting skipped per scope).
Both source repos are marked **proprietary / internal use only** (their own
`LICENSE` files) — adoption here is limited to declarative doctrine (config,
docs, rule shapes), never their application code, per the task's constraint
(no NG4/NG6 app-code copies; only `registry/`, `governance/`, `docs/cto/`).

Classes follow `docs/SOLUTION-CLASSES.md`: template → class → pattern →
enterprise → faang → elite.

## From `/home/akushnir/leaderboard`

| source path | asset | class | verdict | reason |
|---|---|---|---|---|
| `leaderboard/CLAUDE.md` | brain constraints, executor/dispatcher role separation | enterprise | adapt | doctrine informs `charter.yaml` scopeOfAuthority; this repo already has its own AGENTS.md precedence chain, so it's adapted not copied |
| `leaderboard/.instructions.md` | read-only/forbidden action matrix, guard interception | enterprise | adapt | informs `charter.yaml` mayApprove/mayNotApprove split |
| `leaderboard/docs/doctrine/brain-directive-001.md` | Platoon Leader standing order, closure prerequisites | elite | adapt | informs `delivery-lifecycle.yaml` gate discipline (non-negotiable prerequisites before merge) |
| `leaderboard/docs/CTO_OVERLAY.md` | 4-layer governance (Executive/Engineering/DevOps/Support), tiered severity | elite | adopt | direct precedent for a "CTO office" as a governed layer; shape adopted into `charter.yaml` scopeOfAuthority.domains |
| `leaderboard/.cto/config.yaml` | tier system (experimental/standard/critical) | enterprise | reject | this repo already has a closed tier vocabulary (LOW/MED/HIGH/MAX, `persona-card.schema.json`); a second tier taxonomy would drift |
| `leaderboard/.cto/orchestrator.yaml` | CI orchestrator dispatching the 4-layer workflow | enterprise | reject | GitHub-Actions-shaped; this repo's gate of record is `make verify` (Cloud-Build-adjacent doctrine lives in capital-underwriting, not here) |
| `leaderboard/gatekeeper.yaml` | secrets→syntax→lint→tests→cost pipeline, SLA 180s | enterprise | adapt | informs `delivery-lifecycle.yaml` verify stage; this repo's own `scripts/verify.sh` is the actual implementation, not replaced |
| `leaderboard/scripts/gatekeeper` | unified CLI (check/build/fix/squad/cost/version) | enterprise | reject | app tooling, out of scope (declarative-config-only constraint) |
| `leaderboard/docs/LEADERBOARD_PROTOCOL.md` | worktree-per-session isolation, zero-contention registry | elite | adapt | this repo already has its own worktree doctrine (CLAUDE.md); noted as convergent, not re-imported |
| `leaderboard/docs/LEADERBOARD_RULES.md` | standing orders for soldiers/snipers/generals | elite | adapt | informs `abilities.yaml` ability framing (register→verify→claim discipline), renamed to this repo's existing vocabulary |
| `leaderboard/docs/reference/CLOSED_LOOP.md` | brain→worker pipeline: dispatch→sandbox→PR→auto-close | elite | adopt | direct precedent for `delivery-lifecycle.yaml`'s chain shape (dispatch → execute → verify → close) |
| `leaderboard/docs/reference/ELITE_ARCHITECTURE.md` | gVisor micro-VMs, NATS, Temporal, pre-warmed pools | elite | reject | aspirational future-state per the source repo's own doctrine; not adoptable as current practice |
| `leaderboard/docs/reference/FINOPS_ARCHITECTURE.md` | T1/T2/T3 dynamic model-tier routing, semantic cache | elite | adapt | this repo's tier ladder is `docs/MODEL-PROFILES.md` / persona `defaultModelTier`; concept (tiered routing) adopted, exact tier names not |
| `leaderboard/docs/reference/SESSION_ARCHITECTURE.md` | per-session registry, heartbeat, stale detection | enterprise | reject | this repo already has `identity/sso/sessions.py` + `heartbeatSchedule` persona field; convergent, not re-imported |
| `leaderboard/modules.manifest.json` | vendored-module sync gates (syntax/secrets/selfmatch/preflight) | enterprise | adapt | informs `abilities.yaml` fleet-vocabulary-authority ability; this repo's module ownership lives in CODEOWNERS instead |
| `leaderboard/docs/reference/AGENT_TEMPLATES.md` | role templates (commander/platoon-leader/soldier/auditor) | enterprise | adapt | this repo's persona-card posture enum (executor/reviewer/auditor) is the equivalent, already adopted platform-wide; not re-copied |
| `leaderboard/docs/reference/MCP_ENFORCEMENT.md` | guard intercepts forbidden ops, audit log | enterprise | reject | tool-specific (MCP guard mechanics) out of scope for a declarative office module |
| `leaderboard/scripts/guard/*.sh` | per-risk guard scripts (SSRF, vaporware, auth-routes, collision) | enterprise | reject | app/script code, excluded by the declarative-config-only constraint |
| `leaderboard/docs/reference/RULES.md` | normative gate/dispatch/guard ruleset | elite | adapt | informs `charter.yaml` guardrails section framing |
| `leaderboard/docs/reference/FAILURE_TAXONOMY.md` | 15+ failure-mode classification | enterprise | reject | out of this module's scope (delivery/security posture, not failure classification); flagged for a future qa-sme harvest |
| `leaderboard/docs/reference/RUNBOOK.md` | incident classification, rollback procedures | enterprise | reject | operational runbook, out of scope for the CTO office module (belongs with an incident-response module if one is built) |
| `leaderboard/docs/reference/WORKTREE_GOTCHAS.md` | multi-agent collision lessons | enterprise | reject | this repo already documents worktree hazards in its own CLAUDE.md/AGENTS.md |
| `leaderboard/docs/ROADMAP.md` | 12-month epic roadmap | enterprise | reject | source-repo-specific planning artifact, not a reusable doctrine asset |
| `leaderboard/docs/SSOT.md` | single-source-of-truth registry pattern | enterprise | adapt | concept (one canonical index) already realized here via `docs/cto/HARVEST.md` itself and `registry/personas/README.md` |
| `leaderboard/LICENSE` | proprietary, internal-use-only | — | reject | provenance note only: bounds adoption to doctrine/shape, never verbatim text or code |

## From `/home/akushnir/capital-underwriting`

(vendor/leaderboard mirror skipped per scope)

| source path | asset | class | verdict | reason |
|---|---|---|---|---|
| `capital-underwriting/CLAUDE.md` | 28 golden rules (schema migration, secrets, config validation) | elite | adapt | this repo has its own golden-rules chain (`AGENTS.md`, GR-1..24); overlapping rules (no-secrets, IaC-only) already present, not duplicated |
| `capital-underwriting/FLEET_FIRST.md` | fleet-first execution: brain reads, remote workers execute | faang | adapt | informs `delivery-lifecycle.yaml` hermes-as-router framing; this repo's equivalent is the hermes persona card, already present |
| `capital-underwriting/SECURITY.md` | multi-tenant isolation, JWT+TOTP+WebAuthn, secrets in Secret Manager, fail-closed startup validation | enterprise | adopt | direct input to `charter.yaml` security-posture domain and `identity/rbac/presets/cto-superadmin.yaml` break-glass doctrine (explicit, time-boxed, logged) |
| `capital-underwriting/CODEOWNERS` | single-owner review-gate pattern | pattern | adapt | informs `abilities.yaml` merge-approval-gate ability; this repo's actual CODEOWNERS is owned by its own codeowners gate (`make codeowners`), not overwritten |
| `capital-underwriting/docs/architecture/adr/0002-cicd-pipeline-architecture.md` | ADR discipline: record the decision, cite the date it changed | elite | adopt | direct precedent for `charter.yaml` guardrail "Architecture is decided, never improvised — record the decision" |
| `capital-underwriting/infra/terraform/bootstrap/` | two-layer Terraform: bootstrap (once, owner-applied) vs. environments (repeatable) | enterprise | adapt | informs `abilities.yaml` iac-apply-gate ability and `charter.yaml` mayApprove terraform-apply-authorization; this repo's own `infra/` owns the actual Terraform, not touched |
| `capital-underwriting/infra/cloudbuild/pr-gate.yaml` | pre-merge static validation gate | enterprise | adopt | direct precedent for `delivery-lifecycle.yaml` merge stage requiring green verify evidence before the CTO gate opens |
| `capital-underwriting/infra/cloudbuild/emergency-deploy.yaml` | emergency lane that skips IaC validation but keeps boot/canary, requires a logged reason | faang | adapt | informs `charter.yaml` mayApprove security-exception-with-expiry (time-boxed, logged) — the break-glass shape, not the Cloud Build mechanics |
| `capital-underwriting/config/secret-contracts.json` | declared secrets: name, type, TTL, required-status | enterprise | reject | app-config artifact; this repo's secret doctrine is GR-6 + `make verify` secret scan, not a parallel contract file |
| `capital-underwriting/config/rbac-matrix.json` | tenant role/scope matrix | enterprise | adapt | informs `identity/rbac/presets/cto-superadmin.yaml` explicit-allow-list shape; this repo's `identity/rbac/model.py` is the actual engine, not replaced |
| `capital-underwriting/scripts/guard/blocking-gates-allowlist.json` | no gate ships `allowFailure:false` without a recorded green run | enterprise | adopt | direct precedent for the no-false-green proof tests in `registry/personas/tests/test_cto_office.py` (GR-8) |
| `capital-underwriting/scripts/guard/check-schema-drift.sh` | detects declared-vs-actual drift | enterprise | adapt | conceptual precedent for `test_reports_members_resolve_to_real_cards` (declared member vs. actual card file must agree) |
| `capital-underwriting/docs/guides/MODULE_FIRST_DEVELOPMENT.md` | one-keeper-per-module authority matrix | enterprise | adopt | direct precedent for `charter.yaml` mayNotApprove framing (a role's authority is named and bounded, not assumed) |
| `capital-underwriting/docs/guides/FRONTEND_GOVERNANCE.md` | frontend lives outside this repo, mirror only | faang | reject | this repo has its own `portal/` ownership (lane-forbidden to this module per task constraints) |
| `capital-underwriting/docs/OPERATIONS_RUNBOOK.md` | on-call/incident doctrine | enterprise | reject | out of scope for the CTO office module (see leaderboard RUNBOOK.md note above) |
| `capital-underwriting/docs/operations/ACTIVE_WORK.md` | lightweight claim/release registry | pattern | reject | this repo already has `.board/` + issue-claim doctrine (`governance/dispatch`), convergent not re-imported |
| `capital-underwriting/.github/workflows/` | deprecated GitHub Actions (retired after a billing failure) | — | reject | explicit reject per the source repo's own doctrine; also out of scope (CI mechanics, not declarative office config) |
| `capital-underwriting/vendor/saas-rbac/` | RBAC/multi-tenancy keeper module | enterprise | reject | vendored submodule in the source repo; never copied (this repo's own `identity/rbac/` is the equivalent authority) |
| `capital-underwriting/LICENSE` | proprietary, internal use only | — | reject | provenance note only: bounds adoption to doctrine/shape, never verbatim text or code |

## Summary

- **Leaderboard**: 25 rows surveyed — 5 adopt, 13 adapt, 7 reject.
- **Capital-underwriting**: 18 rows surveyed — 5 adopt, 8 adapt, 5 reject.
- **Combined**: 43 rows — 10 adopt, 21 adapt, 12 reject.
- Nothing above was copied as application code (NG4/NG6); every adopted/
  adapted row landed as declarative config or doctrine text under
  `registry/personas/offices/cto/`, `identity/rbac/presets/cto-superadmin.yaml`,
  `integrations/paperclip/`, or this document — never `vendor/`, `.board/`,
  `.fleet/`, `portal/`, `gateway/`, or `governance/pmo/*`.
- Full raw survey output (all rows, both repos, with additional context) is
  preserved in the two harvest-subagent transcripts that produced this table;
  this document is the curated, cited distillation GR-10 requires.
