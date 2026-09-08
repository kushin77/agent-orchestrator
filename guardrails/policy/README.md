# guardrails/policy — Policy-as-code + gate engine

> Owner lane: **guardrails** (issue `kushin77/agent-orchestrator#26`, "22
> Policy-as-code + gate engine (BLOCK/WARN/LOG, startup validation)", work item
> 22, phase 4 — Security & guardrails). Parent: EPIC-00 (issue #4). Doctrine:
> [`../../AGENTS.md`](../../AGENTS.md),
> [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`../../docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md). Lane-local
> provenance: [`PROVENANCE.md`](PROVENANCE.md).

This tree is the **policy-as-code + gate framework** — the guardrail execution
core of the Security & guardrails pillar (pillar 4).  It gives the platform a
declarative rules engine evaluated on every agent action:

* a **policy DSL** (YAML) plus **JSON-Schema startup validation** — an invalid
  or unsafe policy fails the deploy, never at runtime;
* a **gate engine** `evaluate(action, context) -> BLOCK | WARN | LOG` with
  **structured evidence** and fail-closed defaults (AO-GR-19 guard honesty);
* a **controls registry** — every control toggleable and **default OFF**
  (AO-GR-6 flag-gated rollout);
* an **append-only audit seam** — every block is audit-logged;
* an **OPA integration option** for enterprise policy engines.

It runs fully **offline** on the platform standard stack (Python stdlib +
PyYAML); no server, no network, no third-party runtime dependency.

## The contract (30 seconds)

```python
import sys
sys.path.insert(0, "guardrails")             # guardrails/ has no __init__.py yet
from policy import PolicyEngine, DecisionLevel
from policy.controls import ControlRegistry
from policy.startup import build_engine, default_bundle_dir, default_controls_file

reg = ControlRegistry.load_yaml(default_controls_file())  # controls default OFF
engine = build_engine([default_bundle_dir()], controls=reg)

result = engine.evaluate("model.call", tenant="acme",
                         context={"budget": {"utilization_ratio": 0.1}})
result.decision          # DecisionLevel.BLOCK — controls OFF => ungoverned => deny
result.uncovered         # True
```

Flip a control ON (edit `controls.yaml`, `enabled: true` + rationale) and the
same call resolves against the budget policy:

```python
result = engine.evaluate("model.call", tenant="acme",
                         context={"budget": {"utilization_ratio": 1.5}})
result.decision          # DecisionLevel.BLOCK — over budget, evidence attached
result.matched_rules[0].rule_id      # "over-budget-block"
result.blocked           # True
```

## Decision contract

Every evaluation returns an honest tri-state (AO-GR-19):

| Level | Meaning | Result |
|---|---|---|
| `BLOCK` | Deny the action | `DecisionResult.blocked is True` |
| `WARN`  | Allow, but surface a warning | allowed |
| `LOG`   | Allow and observe | allowed |

* **Precedence.** Every applicable rule across every *active* policy is
  evaluated; the **strongest** decision wins (`BLOCK > WARN > LOG`).  A BLOCK
  anywhere is a BLOCK.
* **Policy `default`.** A deny-list policy defaults to `log` (fires `block`
  rules on violations); an allow-list policy defaults to `block` (fires `log`
  rules only for explicitly allowed requests).  When a policy governs the
  action but none of its rules fire, the policy's `default` is contributed.
* **Uncovered action (fail-closed default).** When no *active* policy governs
  the action, the engine returns the configured `uncovered_decision` —
  `BLOCK` by default (deny-by-default: an ungoverned action must not sail
  through).  A deployment observing while it authors policies sets
  `uncovered_decision="log"` explicitly — *unless configured otherwise*.
* **Fail-closed on evaluation error (no-false-green, AO-GR-4).** A rule that
  cannot be evaluated — unknown operator, invalid regex, required context path
  absent — makes the engine return `BLOCK` with the error attached to the
  evidence.  Absence of a required attribute is never a silent pass.

## Policy DSL

One YAML file per policy (a file may instead hold a `policies:` container of
several), validated against [`schema/policy.schema.json`](schema/policy.schema.json):

```yaml
# guardrails/policy/bundles/platform/model-call-budget.yaml
id: model-call-budget          # slug ^[a-z][a-z0-9-]*$, unique in the bundle
version: 1
name: Model call budget
description: >-
  Blocks model calls whose metered utilization has exhausted the tenant budget.
default: log                   # decision when the policy applies but no rule fires
controls: [model-call-budget]  # control id that must be ACTIVE for enforcement
rules:
  - id: over-budget-block
    actions: [model.call]      # glob pattern(s) over the action name
    decision: block            # block | warn | log
    reason: "tenant {tenant} exceeds budget (utilization {budget.utilization_ratio})"
    condition:
      all:
        - path: budget.utilization_ratio
          op: gte              # eq ne gt gte lt lte in not_in glob regex exists missing
          value: 1.0
```

Rule semantics:

* `actions` — non-empty glob patterns matched with `fnmatch` semantics
  (`model.call`, `tool.use`, `egress.send`, `model.*`, `*`).
* `subjects` / `tenants` — optional glob lists that further scope a rule;
  empty means "any".  A rule whose scope does not cover the request is not
  consulted.
* `condition` — an optional predicate tree: `all` (every child), `any` (one
  child), `not` (invert one child), and leaves `{path, op, value}`.  Paths are
  dotted lookups into the merged context and may index lists
  (`request.tags[0]`).  Operators: `exists`, `missing`, `eq`, `ne`, `gt`,
  `gte`, `lt`, `lte`, `in`, `not_in`, `glob`, `regex`.
* `reason` — audit explanation, may interpolate `{path}` tokens from the
  context.  A **BLOCK rule must carry a non-empty `reason`** (an unexplained
  block is unauditable and is rejected by the startup gate).

### Fail-closed condition semantics

A condition leaf that *cannot be evaluated* raises and the engine fails closed
to BLOCK: an unknown operator, a non-numeric comparison, an `in` target that
is not a list, an invalid regular expression, or a **required path absent from
the context**.  `exists` / `missing` are the escape hatch for genuinely
optional attributes — a rule that needs a field fails closed when the platform
did not supply it, rather than silently not firing.

## Bundling & precedence

A **bundle** is a directory of policy files (or an explicit file list), loaded
in sorted order.  The assembler refuses duplicate policy ids (a duplicate would
make "which governs?" ambiguous).  Within a bundle, every applicable rule
across every active policy is evaluated and the strongest decision wins; there
is no hidden ordering among policies.

`PolicyBundle.merge(*others)` overlays bundles in argument order — a later
bundle's policy *replaces* an earlier one with the same id while all other
policies are kept.  This is the base + tenant-overlay seam for per-tenant
policy inheritance (consumed from shared-governance's policy-inheritance
pattern).

## Controls registry — toggleable, default OFF

[`controls.yaml`](controls.yaml) (schema:
[`schema/controls.schema.json`](schema/controls.schema.json)) is the single
declarative registry of every toggleable guardrail control — "a thing a web
toggle flips" (adapted from `CMR guardrails/policy/controls.yaml`):

```yaml
controls:
  - id: model-call-budget
    name: Model call budget enforcement
    description: Activates the model-call-budget policy ...
    enabled: false            # AO-GR-6: every control ships OFF
    mode: block
    implemented_by: [guardrails/policy/bundles/platform/model-call-budget.yaml]
    since: "issue #26"
```

* A control is **active** only when it is registered **and** `enabled: true`.
* A policy is enforced only when `policy.enabled` is true **and** every control
  id it declares is registered AND active.  Referencing an unregistered control
  is a startup-validation error (it would otherwise silently never enforce —
  an AO-GR-4 formality).
* A control that ships `enabled: true` must document `on_since_rationale`;
  `mode: off` requires `enabled: false`.  The registry validator rejects both
  violations.

## Startup validation gate

[`startup.py`](startup.py) implements the deploy-time gate: every policy in a
bundle is checked against the JSON Schema **and** the semantic safety rules
(BLOCK-without-reason, malformed conditions, duplicate ids across the bundle,
control references that resolve).  Invalid → `PolicyValidationError` → nonzero
exit.  An invalid policy fails the deploy, never at runtime:

```bash
python3 guardrails/policy/cli.py validate          # exit 0 = valid bundle
python3 guardrails/policy/cli.py validate <dir>    # exit 1 + every problem
```

The engine additionally fails closed at evaluation time as a second line of
defense, but a malformed bundle should never get that far.

## Audit

Every decision the engine emits is written to its audit log (BLOCK always is).
`AuditRecord` is self-describing: action, subject, tenant, decision, outcome
(`blocked`/`allowed`), contributing policy/rule ids, reason, error, and the
full structured evidence.  [`audit.py`](audit.py) ships `InMemoryAuditLog`
(default engine sink) and `JsonlAuditLog` (append-only JSON-lines file — no
update/delete surface).  The durable, hash-chained, per-tenant tamper-evident
ledger is owned by the observability lane (issue #31); this lane provides the
structured record that ledger consumes.

## OPA integration option

[`opa.py`](opa.py) exposes the pluggable backend seam (issue #26 acceptance #4):
`PolicyBackend` (protocol), `LocalBackend` (adapts the local
`PolicyEngine`), and `OpaBackend`, which posts the action record to an OPA
`v1/data` endpoint over stdlib `urllib` and maps the answer onto
BLOCK/WARN/LOG.  It is an *option*, default OFF — nothing constructs it unless
an OPA endpoint is configured — and every OPA failure (unreachable, non-200,
unparseable body, unknown decision token) **fails closed** to BLOCK with the
error in the evidence.  The transport is injectable, so the adapter is fully
exercised offline by tests against a fake transport; the real OPA binary/server
is not bundled with this lane.

## Shipped example policies

[`bundles/platform/`](bundles/platform) (each gated behind its control, all
controls OFF by default):

| Policy | Control | What it does |
|---|---|---|
| [`model-call-budget.yaml`](bundles/platform/model-call-budget.yaml) | `model-call-budget` | BLOCK at utilization ≥ 1.0, WARN at ≥ 0.8, LOG otherwise |
| [`tool-use.yaml`](bundles/platform/tool-use.yaml) | `tool-use-guard` | BLOCK shell without tenant grant (fail-closed on absent grant), BLOCK exfiltration family, WARN on network probes |
| [`data-egress.yaml`](bundles/platform/data-egress.yaml) | `data-egress-guard` | BLOCK pii/secret egress + cleartext http, WARN sensitive, LOG benign |

## Acceptance criteria (issue #26)

| Criterion | Where |
|---|---|
| Policy DSL + schema + startup validation gate (invalid policy fails deploy, not runtime) | [`loader.py`](loader.py) + [`schemas.py`](schemas.py) + [`startup.py`](startup.py), [`schema/policy.schema.json`](schema/policy.schema.json), [`tests/test_startup_validation.py`](tests/test_startup_validation.py) |
| Gate engine returns BLOCK/WARN/LOG with structured evidence; every block audit-logged | [`engine.py`](engine.py), [`decision.py`](decision.py), [`audit.py`](audit.py), [`tests/test_engine.py`](tests/test_engine.py), [`tests/test_audit.py`](tests/test_audit.py) |
| Controls registry: each control toggleable, default OFF | [`controls.yaml`](controls.yaml), [`controls.py`](controls.py), [`tests/test_controls_registry.py`](tests/test_controls_registry.py) |
| OPA integration option for enterprise policy engines | [`opa.py`](opa.py), [`tests/test_opa_backend.py`](tests/test_opa_backend.py) |
| No-false-green: gates fail closed on unparseable/unknown policy | [`engine.py`](engine.py) fail-closed path, [`tests/test_fail_closed.py`](tests/test_fail_closed.py) |
| Example policies for the platform | [`bundles/platform/`](bundles/platform), [`tests/test_example_policies.py`](tests/test_example_policies.py) |
| README + tests | this file, [`tests/`](tests/) |

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider guardrails/policy/tests -q
```

The suite includes the required **negative controls** (AO-GR-4): every gate and
every schema keyword is probed with a known-bad input and must genuinely fail
(no-false-green).

## Layout

```
guardrails/policy/
├── README.md / PROVENANCE.md     landing doc + provenance (AO-GR-10)
├── schema/*.schema.json          policy + controls JSON Schemas
├── controls.yaml                 controls registry (default OFF)
├── bundles/platform/*.yaml       shipped example policies
├── decision.py model.py          tri-state + policy/rule model
├── conditions.py                 condition tree evaluation (fail-closed)
├── loader.py bundle.py           YAML loading + bundle assembly/precedence
├── controls.py                   controls registry
├── engine.py                     PolicyEngine.evaluate (BLOCK/WARN/LOG)
├── audit.py                      append-only audit log
├── opa.py                        optional OPA backend
├── schemas.py                    dependency-free JSON-Schema subset validator
├── startup.py cli.py             startup gate + CLI (validate/evaluate/controls)
└── tests/                        pytest suite incl. negative controls
```

## Sibling seams (consumed, not redefined)

This lane owns only `guardrails/policy/**`.  It defines the *contract* the
platform's gateable surfaces consult; sibling phase-4 lanes own DLP filters
(issue #27), guard honesty/negative controls (issue #28), tenant isolation
(issue #30) and the tamper-evident ledger (issue #31, telemetry).  Full
ABAC/approval/time authorization machinery lives in the identity phase
(issues #35–#38) and consumes this policy contract; the DSL here carries the
ABAC-ish subject/tenant scoping seed.
