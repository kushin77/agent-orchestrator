---
id: ADR-0029
status: accepted
date: 2026-09-16
deciders: [owner]
req: []
supersedes: []
---

# ADR-0029: Guardrail policy shape for hermes-as-head-of-org and the paperclip operator seam

## Status

`accepted` — closes issue #951 ("gap(module): no guardrail policy for
paperclip or hermes agents", P0, pillar `guardrails-security`), filed against
EPIC #878's lanes L9 (#888, paperclip activation) and L10 (#889, hermes head).
It operates inside the boundaries [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md)
(hermes/paperclip ownership) and [`ADR-0013`](ADR-0013-paperclip-ing-integration.md)
(paperclip operator-surface contract) already fixed; it decides no routing or
integration-mode question either of those records already settled.

## Context

Issue #951's evidence: `grep -ril -E 'paperclip|hermes' guardrails/` returned
nothing before this record, while both agents were already wired into the
gateway (`gateway/providers/hermes.py`, `gateway/providers/paperclip.py`) and
registered as personas (`registry/personas/cards/hermes.yaml`,
`registry/personas/cards/paperclip.yaml`). EPIC #878 proposes hermes as the
fleet's head-of-org agent — elevated authority with **no** corresponding
guardrail control, which #951 calls a safety/admission blocker, not a
completeness gap.

The guardrails pillar already has a working policy-as-code engine
(`guardrails/policy/`, issue #26): a YAML rule DSL evaluated by
`PolicyEngine.evaluate` into `BLOCK`/`WARN`/`LOG` with structured evidence, a
`controls.yaml` registry where every control ships `enabled: false`
(AO-GR-6), and a fail-closed default — an action no active policy governs is
`uncovered` and BLOCKed. Three platform examples
(`bundles/platform/model-call-budget.yaml`, `tool-use.yaml`,
`data-egress.yaml`) and five workbook mechanical-rule policies (issue #636)

> **Amended 2026-09-22:** the `enabled: false` / flag-gated-OFF default cited
> above was reversed by policy-gr5-enabled-by-default (2026-09-21, AO-GR-6);
> see docs/GOLDEN-RULES.md#ao-gr-6--flag-gated-off-by-default.
already ship this shape. The question this record decides is narrow: **what
is the minimal policy declaration for hermes and paperclip that this existing
engine evaluates**, not whether to build a second engine.

## Decision

Two new policy documents ship in `guardrails/policy/bundles/platform/`, each
gated behind its own control in `guardrails/policy/controls.yaml` (both
`enabled: false`), evaluated by the same `PolicyEngine` every other platform
policy uses — **no parallel enforcement path**:

- **`hermes-head.yaml`** (control `hermes-head-guardrails`). An allow-list
  policy (`default: block`) scoped to `subjects: [hermes]`. Allowed: dispatch/
  direct a subordinate agent (`agent.dispatch`, `agent.directive`), but only
  when the request's `channel` context is `gateway`, and model calls under
  the tenant's budget/token ceiling (`budget.utilization_ratio < 1.0`).
  Forbidden, each a **named** BLOCK rule: dispatching outside the gateway,
  self-promotion (`agent.self_promote`), approving a `full` rollout
  (`rollout.approve` with `rollout.level == full`), touching secrets
  (`secrets.access` / `secrets.read` / `secrets.write`), running IaC apply
  (`iac.apply`), and exceeding the budget/token ceiling. Every decision the
  engine returns is written to its audit log — "every directive audited" is
  the engine's existing baseline behaviour (`guardrails/policy/audit.py`),
  not new code.
- **`paperclip-operator.yaml`** (control `paperclip-operator-guardrails`). An
  allow-list policy (`default: block`) scoped to `subjects: [paperclip]`,
  matching the ADR-0013 operator-seam contract exactly: allowed are the three
  contract shapes — ticket (`ticket.update`, `ticket.create`), heartbeat
  (`heartbeat.send`), budget (`budget.query`). Forbidden, each named: code
  execution (`code.execute`, `tool.use`, `shell.exec` — paperclip is an
  operator surface, not a worker) and cross-tenant reads (`tenant.read`).

**Default-deny is enforced at two layers**, both already in the engine and
exercised by neither a new mechanism: (1) within each policy, `default:
block` means any hermes/paperclip action the rules do not explicitly LOG-
allow falls to BLOCK; (2) an action entirely outside a policy's declared
`actions`/`subjects` scope is `uncovered` and falls to the engine's
`uncovered_decision` (BLOCK by default). Both policies stay inert until their
control is flipped ON **and** the corresponding provider flag is on
(`infra/feature-flags/registry.yaml`: `enable_paperclip`, shipped; and
`enable_hermes`, added by sibling lane #950 — read defensively, tolerating
absence, by the new gate script rather than assumed present here).

**Gate.** `scripts/check-guardrail-head-policy.sh` (registered in
`scripts/verify.sh`) proves: both controls registered and default OFF; every
forbidden action refused **by name** (the matched rule id, not a generic
BLOCK); every allowed action passes; default-deny for both layers; the
provider flags are read read-only; and a negative control — removing
`hermes-head.yaml` from the bundle directory and re-evaluating an
otherwise-allowed hermes action must turn `uncovered`/BLOCK, proving the gate
actually depends on the policy file rather than passing by construction.

## Consequences

- **Positive:** hermes carries a concrete, testable authority boundary before
  EPIC #878 can propose it as head-of-org in earnest; paperclip's ADR-0013
  operator-seam contract now has an enforcement surface, not just a design
  doc. No new engine, no new enforcement code path — the existing
  `guardrails/policy/` startup gate, controls registry and audit log absorb
  both agents exactly as they absorb every other platform policy.
- **Negative:** the action vocabulary this record introduces
  (`agent.dispatch`, `agent.directive`, `agent.self_promote`,
  `rollout.approve`, `secrets.access`, `iac.apply`, `ticket.update`,
  `heartbeat.send`, `budget.query`, `code.execute`, `tenant.read`) is not yet
  emitted by a real call site — the gateway providers
  (`gateway/providers/hermes.py`, `gateway/providers/paperclip.py`) are
  inference-only adapters (ADR-0012 Context §5) and do not yet call
  `PolicyEngine.evaluate` before a directive/ticket/heartbeat/budget action.
  Wiring an actual call site is follow-up work for #888/#889, out of scope
  for `guardrails/**`.
- **Neutral:** this record makes no routing, integration-mode, or ownership
  decision — ADR-0012 and ADR-0013 stand unamended. It is reversible at the
  cost of two YAML files and two controls-registry entries.

**Follow-ups.** #888/#889 (or a successor) should call
`PolicyEngine.evaluate` from the actual hermes/paperclip call sites once
those exist, so these policies stop being evaluable-but-uncalled and start
gating real traffic; that wiring lives outside `guardrails/**` and is not
this record's owned surface.
