# guardrails — Security & Guardrails (pillar 4, phase 4)

Owner lane: **guardrails**. See [`../docs/EXECUTION-PLAN.md`](../docs/EXECUTION-PLAN.md).

## Purpose

Policy gates and defense-in-depth for agent traffic: DLP filters,
prompt-injection defense, policy-as-code gates, budgets/FinOps enforcement
(EPIC-00, issue #4). Security lane — escalation to a higher model tier is
expected.

## Planned contents (phase 4, issues #26–#30)

- DLP filters and content policy gates.
- Prompt-injection / exfiltration defenses.
- Policy-as-code bundles enforced at the gateway/engine boundary.

## Status

Placeholder scaffold from issue #5. No implementation yet.

## Head-of-org guardrails: hermes and paperclip (issue #951)

`guardrails/policy/bundles/platform/hermes-head.yaml` and
`guardrails/policy/bundles/platform/paperclip-operator.yaml` are the guardrail
policy entries for the two agents named in EPIC #878's "head of the org" seam
([`ADR-0012`](../docs/decision-records/ADR-0012-hermes-paperclip-boundary.md),
[`ADR-0013`](../docs/decision-records/ADR-0013-paperclip-ing-integration.md)).
Design record: [`ADR-0029`](../docs/decision-records/ADR-0029-hermes-head-guardrails.md).
Both are declared and enforced by the **existing** `guardrails/policy`
gate engine — no parallel evaluation path. Full contract and DSL reference:
[`policy/README.md`](policy/README.md).

| | Hermes (head-of-org) | Paperclip (operator seam) |
|---|---|---|
| Control (`controls.yaml`, default `enabled: false`) | `hermes-head-guardrails` | `paperclip-operator-guardrails` |
| Policy | `bundles/platform/hermes-head.yaml` | `bundles/platform/paperclip-operator.yaml` |
| Allowed (LOG) | dispatch/direct via the gateway only (`channel == gateway`); model calls under the budget/token ceiling | ticket update/create, heartbeat, budget query — the three ADR-0013 contract shapes, nothing else |
| Forbidden (named BLOCK rules) | dispatching off-gateway, self-promotion, approving a `full` rollout, touching secrets, running IaC apply, exceeding the budget/token ceiling | code execution (`code.execute`/`tool.use`/`shell.exec`), cross-tenant reads |
| Default-deny | policy `default: block` (unlisted hermes action) + engine `uncovered_decision` (action outside declared scope) — both BLOCK | same two-layer default-deny |
| Flag gate | control OFF by default; also gated by the platform's `enable_hermes` flag (`infra/feature-flags/registry.yaml`, added by sibling lane #950 — read defensively, absence tolerated) | control OFF by default; also gated by `enable_paperclip` (`infra/feature-flags/registry.yaml`, shipped) |

**Gate:** `scripts/check-guardrail-head-policy.sh` (registered in
`scripts/verify.sh`) proves both controls default OFF, every forbidden action
refused by name, every allowed action passing, default-deny at both layers,
and — the required negative control — that removing
`bundles/platform/hermes-head.yaml` from the bundle turns an otherwise-allowed
hermes action `uncovered`/BLOCK, so the gate is proven to depend on the
policy file rather than passing by construction.

**Tests:** `guardrails/policy/tests/test_head_of_org_guardrails.py` (23 cases
— named refusals, allowed passes, default-deny, audit-record assertions,
fail-closed-by-default, and cross-subject scoping).

**Canary (issue #1519):** the gate above and the suite above both build their
"control ON" registry **in-process, in a fixture** — so until #1519 the policies
had never been exercised against a committed registry, and a guardrail that has
never fired anywhere is a declaration, not a control.
[`policy/canary/controls.canary.yaml`](policy/canary/controls.canary.yaml) is a
committed, non-production registry with `hermes-head-guardrails` ON, scoped to
`policy/bundles/platform/hermes-head.yaml` alone. It is the scope in which one
BLOCK is proven to fire, by name (`block-directive-off-gateway`), through the
real CLI and the real `policy/audit.py` ledger; the negative control (same
action, `channel == gateway`) passes. Result, commands and the
`enable_hermes` boundary: [`policy/canary/README.md`](policy/canary/README.md).
This is a PROOF, not a promotion — `policy/controls.yaml` still ships the
control `enabled: false`.

**Not yet wired:** the gateway provider adapters
(`gateway/providers/hermes.py`, `gateway/providers/paperclip.py`) are
inference-only (ADR-0012 Context §5) and do not yet call
`PolicyEngine.evaluate` before a directive/ticket/heartbeat/budget action —
these policies are evaluable today, not yet consulted by a real call site.
Wiring that call site is follow-up work outside `guardrails/**` (owned by
#888/#889), named in ADR-0029's follow-ups.
