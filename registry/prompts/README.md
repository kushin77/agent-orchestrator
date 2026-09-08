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

The authoritative shape lives in [`prompt-module.schema.json`](prompt-module.schema.json);
every module file under `modules/` is validated against it, and the whole
definition is content-addressed.

### Publishing freezes a version

- A version becomes resolvable only when **published**. Publishing appends it
  to [`manifest.yaml`](manifest.yaml) with a content digest
  (sha256 of the canonical YAML) and pins runtime resolution to it.
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

## Directory layout

| Path | What it holds |
|---|---|
| [`prompt-module.schema.json`](prompt-module.schema.json) | PromptModule JSON Schema (the contract). |
| [`registry.py`](registry.py) | Registry API + CLI: register / get / publish / pin / resolve / render / governance. |
| [`feedback.py`](feedback.py) | Feedback loop: outcome labels to per-prompt-version FP/FN metrics + CLI report. |
| [`abtest.py`](abtest.py) | A/B variant runner for prompt improvements + offline demo. |
| [`manifest.yaml`](manifest.yaml) | Version manifest: published (frozen) versions per taskType + pinned version + digests. |
| `modules/` | PromptModule definitions, one YAML file per `taskType.version`. |
| `bodies/` | Prompt body content (system/user markdown) referenced by `bodyRefs`. |
| `output-schemas/` | JSON Schema output contracts referenced by `outputSchema`. |
| `seed/` | Sample data: `feedback-events.yaml`, `ab-test-runs.yaml`, `call-plan.yaml`. |
| `tests/` | pytest suite (version immutability, pinned resolution, refusal, feedback math). |

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
python3 registry/prompts/registry.py publish summarize v2   # freezes a new version
python3 registry/prompts/registry.py pin summarize v1       # rollback resolution

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

## Verification

- `python3 -m pytest registry/prompts/tests -q` — 25 tests (version
  immutability: publish-twice fails and edited published content is an
  integrity violation; pinned resolution; unregistered taskType refused;
  governance violation; feedback FP/FN math).
- `make verify` stays green (all added YAML/JSON parse; markdown links
  resolve; no trailing whitespace; no unfinished markers).
- Negative control (no-false-green): `resolve ad-hoc-inline` and the
  publish-twice path both exit nonzero with a clean error.
