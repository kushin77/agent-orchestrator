# workbook-mechanical-rules — the five workbook rules as mechanical policy gates

> Owner lane: **guardrails** (issue `kushin77/agent-orchestrator#636`, work
> item `workbook-5`; parent epic `#631`; source workbook `#614`). Doctrine:
> [`../../AGENTS.md`](../../AGENTS.md),
> [`../policy/README.md`](../policy/README.md). Lane-local provenance:
> [`../policy/PROVENANCE.md`](../policy/PROVENANCE.md).

This document is the contract for the five **mechanical enforcement rules** of
the enterprise workbook's Tab 3, shipped as named policy ids in
[`bundles/platform/workbook-mechanical-rules.yaml`](bundles/platform/workbook-mechanical-rules.yaml)
and gated behind five default-OFF controls in
[`controls.yaml`](controls.yaml).

## 1. What "mechanical" means here

The workbook calls these *mechanical* enforcement rules. The taxonomy is
deliberate and this lane holds to it strictly:

| Kind | Decision input | Example |
|---|---|---|
| **Mechanical** | a value a producer **publishes**, compared to a constant | `prefetch.frontloaded == true` → refuse |
| Advisory | a reviewer's judgement | "the plan looks reasonable" |
| Prose | a natural-language instruction | "prefer not to frontload memory" |

A prose rule is a **formality** under AO-GR-4: its pass and fail paths collapse
because nothing can decisively fail. Every rule shipped here is of the first
kind — one boolean, one comparison, one decision — and
`tests/test_example_policies.py::test_every_workbook_rule_has_a_mechanical_condition`
enforces that shape, rejecting any rule whose condition is absent or is not a
`{path, op, value}` leaf over a boolean.

## 2. The five rules and where they come from

The rules are the mechanical half of the C-suite responsibilities in the
workbook's org-chart table (#614), gap-analysed in #631:

| # | Persona | Workbook responsibility | Policy id |
|---|---|---|---|
| 1 | CEO | "goal decomposition into tickets" | `workbook-vector-memory-frontload` |
| 2 | CTO | "automated draw.io diagram generation" | `workbook-drawio-mcp-diagramming` |
| 3 | COO | "external state machine sync" | `workbook-external-state-caching` |
| 4 | CFO | "deterministic Python script execution" | `workbook-zero-token-arithmetic` |
| 5 | CMO | "GoHighLevel webhook management" | `workbook-webhook-caching` |

Four of the five are *cache what should be cached* rules; the fifth is the
mirror image — *do not serve stale context*. That asymmetry is why the bundle
declares two enforcement shapes rather than five copies of one.

## 3. The two enforcement shapes

### 3.1 REFUSE — a deny-list gate

Used by rule 1. The policy's `default` is `log`; one rule **blocks** on
positive evidence of the discouraged thing:

```yaml
- id: workbook-vector-memory-frontload
  default: log
  rules:
    - id: refuse-frontloaded-vector-memory
      actions: [workbook.prefetch]
      decision: block
      condition: {path: prefetch.frontloaded, op: eq, value: true}
```

`prefetch.frontloaded == true` means a vector memory *was* frontloaded, and the
call is refused with a reason naming the rule. `false` — a genuinely fresh
prefetch — falls through to `log`. Why `refuse` rather than `flag`: a
frontloaded vector memory is the stale-context failure mode a prefetch exists
to avoid, so it is denied outright.

### 3.2 CACHE — a statement-of-intent gate

Used by rules 2–5. The policy's `default` is `block`; one rule fires a `log`
decision when the producer declares the value cacheable:

```yaml
- id: workbook-webhook-caching
  default: block
  rules:
    - id: allow-cacheable-webhook-payload
      actions: [workbook.webhook]
      decision: log
      condition: {path: webhook.cacheable, op: eq, value: true}
```

This shape answers **both** readings of "cache this" mechanically:

* *allow-list reading* — a value declared `cacheable: true` is allowed, and the
  allow-list rule is the recorded evidence.
* *refuse-list reading* — `cacheable: false` (do not cache yet) **and** an
  **absent** `cacheable` attribute (we have no idea) both resolve to the
  policy default, `block`, and are refused.

The absence case is the important one: the engine treats a required path that
is missing as a fail-closed evaluation error, so an un-declared value can never
sail through as a silent pass. That is mechanical in the strongest sense —
the *only* way to be allowed is to publish the affirming boolean.

> **Why `block` and not `warn` for the refusal branch?** A `warn` would allow
> the action and merely flag it, which for a cache-coherence rule means the
> stale/undeclared value still flows. For a fail-closed guardrail lane the
> stricter level is the correct default; an operator who wants to observe
> first flips the *control* rather than weakening the *rule*.

## 4. Controls (all OFF)

Each policy is gated behind its own control — the same id as the policy — and
every control ships `enabled: false` (AO-GR-6). With a control OFF the policy
is **inactive**, so the action is uncovered and the engine fails closed to
BLOCK; flipping the control ON is the deliberate, reviewed act that activates
the rule.

```bash
# inspect
python3 guardrails/policy/cli.py controls
# validate the bundle (schema + semantic + control-reference checks)
python3 guardrails/policy/cli.py validate
```

## 5. Semantics per rule

| Policy id | Action | Gate attribute | Affirmative → | Anything else → |
|---|---|---|---|---|
| `workbook-vector-memory-frontload` | `workbook.prefetch` | `prefetch.frontloaded` | BLOCK (refuse) | LOG (fresh prefetch, allowed) |
| `workbook-drawio-mcp-diagramming` | `workbook.drawio` | `drawio.mcp_tool_list_cacheable` | LOG (allowed) | BLOCK (refuse) |
| `workbook-external-state-caching` | `workbook.external_state` | `external_state.cacheable` | LOG (allowed) | BLOCK (refuse) |
| `workbook-zero-token-arithmetic` | `workbook.arithmetic` | `arithmetic.cacheable` | LOG (allowed) | BLOCK (refuse) |
| `workbook-webhook-caching` | `workbook.webhook` | `webhook.cacheable` | LOG (allowed) | BLOCK (refuse) |

Note the inversion on rule 1: there, `true` is the *undesirable* state and the
decision is BLOCK; on rules 2–5 `true` is the *desirable* state and the
decision is LOG. The gate attribute always names the fact being asserted, and
the decision is derived from it — never the reverse.

## 6. Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest guardrails/policy -q -p no:cacheprovider
```

The suite proves, for every rule: the refusal branch refuses and is audited;
the affirmative branch is a real allow-list *hit* (not a silent pass); the
absent-attribute branch fails closed with an error attached; each policy is
control-gated and inert while its control is OFF; and each rule's condition is
a mechanical `{path, op, value}` boolean leaf.

## 7. Related

* [`../sandbox/runtime-enablement.md`](../sandbox/runtime-enablement.md) — the
  sandbox runtime enablement flag (the other half of #636).
* [`../sandbox/README.md`](../sandbox/README.md) — the sandbox contract these
  rules execute under.
* `#631` (parent epic), `#614` (source workbook), `#26` (policy-as-code + gate
  engine), `#343` (server-side guardrail control surface).
