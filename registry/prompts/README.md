# Versioned Prompt / Instruction Library

The **governance primitive for every model call** in the control plane: prompt
modules (one per task type), immutable published versions, output schemas, a
feedback loop, and an A/B variant runner. This directory is the phase-1
contract for the Agent Registry & Profiling pillar (`registry/`, lane owned by
issue #13). Later phases — the model gateway (phase 2) and the guardrails
pillar — consume this contract: a runtime model call **never** ships an
ad-hoc, unversioned prompt; it resolves a pinned prompt module through the
registry API below.

Parent issue: `kushin77/agent-orchestrator#13` (09 versioned prompt library).
Pillar scope per [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md)
(one issue = one lane; this lane owns `registry/prompts/**` only).

## The contract

Every model call at runtime references a **PromptModule**:

| Field | Type | Meaning |
|---|---|---|
| `taskType` | string `^[a-z][a-z0-9-]*$` | Kebab-case task identifier; the registry key a runtime resolves on. |
| `version` | string `^v[0-9]+$` | Version label (`v1`, `v2`, ...). A published `(taskType, version)` is immutable. |
| `description` | string | What the module is for and when to use it. |
| `modelTierHint` | enum `low\|med\|high` | Cheapest model tier that can do the task: `low` = flash/LOW, `med` = balanced, `high` = pro/HIGH (execution-plan tier ladder). The phase-2 gateway routes on this. |
| `modelFamilyHint` | string | Optional family preference (e.g. `flash/LOW`). |
| `status` | enum `draft\|published\|deprecated` | Lifecycle state. Only published versions resolve at runtime. |
| `parameters` | object | Optional sampling parameters (temperature/topP/maxTokens), from the prompt_tuner LLMParameters pattern. |
| `bodyRefs` | map `{system?, user?, assistant?, examples?}` | Named pointers (relative to the module file) to the prompt body content. |
| `outputSchema` | string | Relative reference to the JSON Schema the model output must satisfy. |
| `enforcement` | object `{policyId, action, attribute, rule?}` | The mechanical enforcement rule this module's prompt makes observable, paired to the `guardrails/policy` policy id that gates it. |
| `provenance` | string[] | GR-10 provenance records: where the module's content was adapted from. |

The authoritative shape lives in [`prompt-module.schema.json`](prompt-module.schema.json);
every module file under `modules/` is validated against it, and the whole
definition is content-addressed.

### Publishing freezes a version

- A version becomes resolvable only when **published**. Publishing appends it
  to [`manifest.yaml`](manifest.yaml) with a content digest
  (sha256 of the canonical YAML) and pins runtime resolution to it.
- **Publishing is eval-gated.** Before the manifest is touched, `publish` runs
  the module's **regression evals** ([`evals.py`](evals.py)). A module whose
  cases fail, or which carries no cases at all, is refused outright — an
  unevaluated module is never silently green. A refused publish writes nothing,
  so the version cannot later resolve as if it had been frozen.
- **A published version is immutable**: publishing the same `(taskType,
  version)` twice fails, and editing a published module's definition is
  detected as an integrity violation on the next resolve (the digest no longer
  matches). Improving a prompt means publishing a **new** version.
- `pin(taskType, version)` rolls resolution back to an already-published
  version without violating immutability.
- Runtime resolution (`resolve(taskType)`) returns the pinned published
  version, verifies its digest, and confirms every `bodyRefs` target and the
  `outputSchema` file exist and parse. **Unregistered taskTypes are refused**
  (governance rule — see below).

## The regression-eval gate

A published prompt is only as good as the last time its behavior was measured,
so the measurement is a **precondition of publication**. [`evals.py`](evals.py)
holds the harness and `evals/eval-cases.yaml` holds the cases:

```yaml
cases:
  - caseId: ceo-decompose-goal
    promptId: ceo-primary@v1
    expected: [tickets-decomposed, memory-frontloaded]
    observed: [tickets-decomposed, memory-frontloaded]
```

`expected` is the ground truth and `observed` what the run actually produced, so
the pair is handed to [`feedback.py`](feedback.py)'s own FP/FN math — there is no
second scoring implementation to drift. A case passes iff it has **no false
positive and no false negative**; a case with no observed labels fails its
expectations rather than vacuously passing.

| Verdict | Condition | Publish |
|---|---|---|
| PASS | every case has FP=0 and FN=0 | allowed |
| FAIL | any case has an FP or FN | **refused** |
| UNEVALUATED | no cases for the promptId | **refused** |

`evals/eval-cases.yaml` also carries an `ao-empty@v1` **candidate** that always
fails and has no module definition. It is the standing negative control: a gate
whose pass and fail paths could collapse is a formality, so this fixture proves
the refusal path is real on every run.

## The C-suite Master AI Prompt Templates (workbook-8, issue #639)

The five workbook *Master AI Prompt Templates* ship as published modules. Each
one's mechanical enforcement rule is paired to the workbook-5 policy id
declared by the guardrails lane (issue #636,
[`../../guardrails/policy/workbook-mechanical-rules.md`](../../guardrails/policy/workbook-mechanical-rules.md)) —
this lane is a **read-only consumer** of those ids and records the pairing in
the module's `enforcement` block:

| Module | Seat | Pole | Enforcement rule | Policy id | Action | Gate attribute |
|---|---|---|---|---|---|---|
| `ceo-primary@v1` | CEO | high | vector memory lookup & context frontloading | `workbook-vector-memory-frontload` | `workbook.prefetch` | `prefetch.frontloaded` |
| `cto-primary@v1` | CTO | high | draw.io MCP diagramming + multi-repo AST index caching | `workbook-drawio-mcp-diagramming` | `workbook.drawio` | `drawio.mcp_tool_list_cacheable` |
| `coo-primary@v1` | COO | med | external state caching + Prometheus delta ingestion | `workbook-external-state-caching` | `workbook.external_state` | `external_state.cacheable` |
| `cfo-primary@v1` | CFO | low | deterministic script execution (zero-token arithmetic) | `workbook-zero-token-arithmetic` | `workbook.arithmetic` | `arithmetic.cacheable` |
| `cmo-primary@v1` | CMO | med | webhook caching + template injection | `workbook-webhook-caching` | `workbook.webhook` | `webhook.cacheable` |

The `taskType` is `<role>-primary` rather than `<role>/primary` because the
schema constrains a taskType to `^[a-z][a-z0-9-]*$` — the `/primary@v1` form
the C-suite PersonaCards (issue #632) carry in `systemPromptRef` is the
**logical** prompt id; `role/primary@v1` ↔ `role-primary@v1` is the same module
under the registry's kebab-case key, and `tests/test_evals.py` asserts the two
agree, along with the tier ladder.

## Directory layout

| Path | What it holds |
|---|---|
| [`prompt-module.schema.json`](prompt-module.schema.json) | PromptModule JSON Schema (the contract). |
| [`registry.py`](registry.py) | Registry API + CLI: register / get / publish / pin / resolve / render / governance. |
| [`feedback.py`](feedback.py) | Feedback loop: outcome labels to per-prompt-version FP/FN metrics + CLI report. |
| [`evals.py`](evals.py) | Regression-eval harness: eval cases to a pass/fail verdict, plus the publish gate's `require_ok`. |
| [`abtest.py`](abtest.py) | A/B variant runner for prompt improvements + offline demo. |
| [`manifest.yaml`](manifest.yaml) | Version manifest: published (frozen) versions per taskType + pinned version + digests. |
| `modules/` | PromptModule definitions, one YAML file per `taskType.version`. |
| `bodies/` | Prompt body content (system/user markdown) referenced by `bodyRefs`. |
| `output-schemas/` | JSON Schema output contracts referenced by `outputSchema`. |
| `evals/` | Regression-eval cases per promptId (the publish gate's data). |
| `seed/` | Sample data: `feedback-events.yaml`, `ab-test-runs.yaml`, `call-plan.yaml`. |
| `tests/` | pytest suite (version immutability, pinned resolution, refusal, feedback math, eval gate). |

## Governance rule (all model calls MUST reference a module)

> **No unversioned ad-hoc prompts at runtime.** Every model call references a
> registered, published prompt module (a taskType pinned to a frozen version)
> and that module declares an output schema.

Enforcement is mechanical, not prose:

- `registry.resolve(taskType)` raises `UnknownTaskTypeError` for any taskType
  that is not registered and published — a runtime cannot accidentally call
  with an ad-hoc prompt string.
- `registry.render_prompt(taskType, variables)` renders the **frozen** bodies
  with template substitution and refuses to return a prompt with leftover
  `{{...}}` placeholders (the dispatch convention).
- `registry.governance_check(call_plan)` audits a call plan (list of taskType
  references) and reports every reference that does not resolve. The CLI
  returns exit code 1 on any violation — no-false-green.
- The compliant sample is [`seed/call-plan.yaml`](seed/call-plan.yaml).

Phase-2 (model gateway) is expected to route only through `resolve()` +
`render_prompt()` and to validate every model output against the resolved
module's `outputSchema`.

## Usage

All commands run from the repo root; the modules are self-contained Python 3
scripts (stdlib + jsonschema + PyYAML; no network, no third-party installs).

```bash
# Registry: inspect / resolve / render / govern
python3 registry/prompts/registry.py status
python3 registry/prompts/registry.py resolve classify-route
python3 registry/prompts/registry.py render code-review-verdict \
  --var diff="$DIFF" --var context="flag-gated surface"
python3 registry/prompts/registry.py governance registry/prompts/seed/call-plan.yaml
python3 registry/prompts/registry.py evals ceo-primary v1      # eval gate for one module
python3 registry/prompts/registry.py publish summarize v2   # freezes a new version
python3 registry/prompts/registry.py pin summarize v1       # rollback resolution

# Regression evals: pass/fail per promptId
python3 registry/prompts/evals.py report
python3 registry/prompts/evals.py report --fail-on-eval-failure

# Feedback loop: per-prompt-version FP/FN report
python3 registry/prompts/feedback.py report

# A/B variant runner: reproducible offline demo + recorded-run report
python3 registry/prompts/abtest.py demo
python3 registry/prompts/abtest.py report

# Tests
python3 -m pytest registry/prompts/tests -q
```

### What the outputs show

- `feedback.py report` aggregates `seed/feedback-events.yaml` and prints, per
  prompt version, per-label TP/FP/FN with precision/recall/F1 plus total FP/FN
  and overall accuracy. The seed shows `classify-route` v2 reducing FP/FN
  versus v1 (the loop driving an improvement).
- `abtest.py demo` evaluates the v1 baseline prompt against a v2 disambiguation
  candidate on labeled eval cases through a deterministic keyword stand-in for
  a live model, and selects the winner (candidate: accuracy 1.000 vs baseline
  0.714). `abtest.py report` reproduces the same numbers from the recorded
  `seed/ab-test-runs.yaml`, so experiment outcomes are auditable offline.

## Provenance (cannibalized and adapted)

Adapted from the sources indexed in
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md); each was read
through the `.research/` read-only mirrors (GR-10). None are copied verbatim —
the module schema, registry, and CLI are this repo's own generalization.

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `gmail-agent` `src/agent/prompts/<task>/v1.ts` | Versioned prompt + OutputSchema per task (`task/v1` identity) | `taskType` + `version` + `outputSchema` fields; `modules/` + `bodies/` split. |
| `llm-triage` `feedback.py` | Per-label FP/FN confusion + precision/recall/F1 | `feedback.py` `LabelMetrics`/`PromptVersionMetrics`. |
| `llm-triage` `few_shot.py` / `classifier.py` | Labeled-example evaluation shape | Labeled eval cases in `abtest.py` + `seed/ab-test-runs.yaml`. |
| `intelligence` `prompt_tuner.py` | Prompt-variant A/B + select-best-variant + parameters | `abtest.py` `AbExperiment`/`select_best`, `parameters` in the schema. |
| `CMR` `guardrails/instructions/prompt-library.md` + `guardrails/prompts/` | Metadata-tagged, templated, schema-constrained, versioned assets | Body/ref split, frozen versions, render-leftover rule. |
| `gov-ai-scout` `ai-provider.ts` | zod-validated per-task output schemas | `output-schemas/*.schema.json`. |

### workbook-8 provenance (issue #639)

The five C-suite modules are **adapted** from the enterprise workbook's Tab 3
*Master AI Prompt Templates* (issue
[#614](https://github.com/kushin77/agent-orchestrator/issues/614)) — their
headline rule, persona and pillar — on top of the org-chart rows and the
PersonaCards shipped by issues #631/#632, and are paired to the policy ids
declared by issue #636. The workbook text is **not copied verbatim**: each
system body is rewritten to this repo's conventions (our ticket/lane/verification
vocabulary, our output-schema rules) and every adaptation records its source in
the module's `provenance` list for GR-10. The workbook is a *source*, never a
specification — where it conflicts with `AGENTS.md` the repo doctrine wins.

## Verification

- `python3 -m pytest registry/prompts/tests -q` — 55 tests (version
  immutability: publish-twice fails and edited published content is an
  integrity violation; pinned resolution; unregistered taskType refused;
  governance violation; feedback FP/FN math; the regression-eval harness and
  the publish gate's refusal of failing *and* unevaluated modules; the five
  C-suite modules' render, tier and policy-id mapping).
- Negative control (no-false-green): `resolve ad-hoc-inline` and the
  publish-twice path both exit nonzero with a clean error, the publish gate
  refuses a failing module (`ao-empty@v1`) and leaves the manifest untouched,
  and `scripts/check-docs.sh`-visible text carries no unfinished markers.
- Mutation proof: disabling the publish gate's eval check
  (`registry/prompts/registry.py`, `sha256:e38030a0…f920` →
  `sha256:b8ad0824…d9df`) turns **3** tests red (`3 failed, 52 passed`); the
  restored file is byte-identical and the suite is green again (`55 passed`).
