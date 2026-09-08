# guardrails/policy — Provenance (AO-GR-10)

Lane-local record of assets consumed to build the policy-as-code + gate
framework (issue `kushin77/agent-orchestrator#26`).  Every asset below was
verified present in the local clone, consumed as a **PATTERN / READY shape**,
and **adapted** (not copied) for a multi-tenant SaaS control plane on the
Python-stdlib + PyYAML stack.  Sources are read-only clones under
`/home/akushnir/agent-orchestrator/.research/` (gitignored; never committed).
The repo-wide harvest index is `docs/CANNIBALIZATION.md` (issue #8) — this
file is the lane-local pointer per AO-GR-10 and the issue's "verify each path,
record provenance" instruction.  All sources are internal `kushin77` fleet
repositories.

| Source repo (local path) | Asset (repo-relative) | Verdict | How it was adapted here |
|---|---|---|---|
| `kushin77/defragsuite` (`.research/fleet/defragsuite`) | `pkg/defrag-ai/interceptor.go` | PATTERN | Executable BLOCK/WARN/ALLOW rule engine + detection evidence + rate limiting → the tri-state decision contract (`decision.py`, `engine.py`); no LLM enrichment / rate limiter here. |
| `kushin77/defragsuite` (`.research/fleet/defragsuite`) | `services/legacy/defrag_ai/policies/policies.yml`, `schema.json`, `validate_policies.py` | READY (shape) | Policy DSL in YAML + JSON-Schema + startup `validate_policies` gate → policy document shape, `schema/policy.schema.json`, and `startup.py` deploy gate. The source depends on third-party `jsonschema`; this lane's stack is stdlib + PyYAML, so schema validation is re-implemented by the dependency-free subset validator in `schemas.py` (offline, deterministic, itself negative-tested). |
| `kushin77/shared-governance` (`.research/fleet/shared-governance`) | `governance/policy-dsl/*` (`policy_dsl.py`, `policy_versioning.py`, `example-policies.yaml`), `GLOBAL_STANDARDS/schemas/*` | PATTERN | Rule/effect/severity/condition DSL + policy versioning + hierarchical inheritance → the rule model (`model.py`), per-policy `version`, and `PolicyBundle.merge` overlay seam. The source targets OPA/Rego; this lane is OPA-optional. |
| `kushin77/leaderboard` (`.research/leaderboard`) | `config/policy.yaml`, `config/policies/{base.yaml,rules/*.yaml,invariants/*.yaml}`, `scripts/guard/policy-check.sh`, `lib/guard.sh` | PATTERN | Machine-readable non-negotiables with enforcement/status + modular base/rules YAML + policy check → the modular bundle-of-YAML-files layout (`bundles/platform/`) and the "check that genuinely fails" doctrine (AO-GR-4 / no-false-green). |
| `kushin77/CMR` (`.research/CMR`) | `guardrails/policy/controls.yaml`, `guardrails/policy/controls.schema.json`, `guardrails/policy/controls_policy.py`, `guardrails/gates/**` | PATTERN | Controls registry (one row per control, `enabled: false` default OFF, `mode`, `implemented_by`, `since`, `on_since_rationale`) → `controls.yaml` + `controls.py` + `schema/controls.schema.json`. The source's drift validator (registered-but-missing / runs-but-unregistered) maps here to: policy→control references that fail startup when unregistered. |
| `kushin77/git-rca-workspace` (`.research/fleet/git-rca-workspace`) | `src/policy/opa_client.py` | PATTERN | OPA policy client (sync/batch/health) → `opa.py` `OpaBackend` (v1/data POST + decision mapping). The source uses `requests`/`aiohttp`; this lane's OPA option uses stdlib `urllib` with an injectable transport so it is exercised offline and fails closed on any transport error. |

No assets were copied verbatim; identifiers, module structure, and runtime
behavior were redesigned for this repo's pillar layout, doctrine
(`AGENTS.md`, `docs/GOLDEN-RULES.md`), and stack constraints.  No licenses were
violated — all sources are internal `kushin77` fleet repositories of this
organization.
