# Chat quality loop — prompt modules, the eval harness and the feedback loop

The quality half of the conversational surface (issue `#509`, parent `#500`,
contract `ADR-0023`). Three controls, each of which can genuinely fail:

1. **Chat prompt modules** ([`modules/`](modules)) — versioned, frozen, with a
   declared `outputSchema` that **requires the citations envelope**. The
   registry refuses a module whose schema merely permits it, so a grounded
   answer that names no source cannot validate.
2. **The offline eval harness** ([`eval/`](eval)) — five declared cases, one per
   quality risk, with declared expectations. An expectation that is not met
   fails the case **by name**; a case that could not be run is `CANNOT-ASSESS`,
   never a pass.
3. **The feedback loop** ([`feedback.py`](feedback.py)) — per-message feedback
   keyed to `(prompt module version, model, tier)`, projected into the
   **existing** per-version FP/FN metrics vocabulary the prompts view renders.

[`regression.py`](regression.py) is the gate over all three: a changed module
version is re-evaluated against the fixtures before promotion, and a version
that regresses a case is refused **by name**.

## The module contract

Every chat prompt module is a YAML definition validated against
[`prompt-module.schema.json`](prompt-module.schema.json): `taskType`, `version`,
frozen `bodyRefs`, `modelTierHint`, `groundingPolicy` and the `outputSchema` the
model output must satisfy. Same shape as the control-plane prompt library
([`registry/prompts/`](../prompts/README.md)) — a module is the governance
primitive for a model call, and a published `(taskType, version)` is immutable,
so improving a prompt means publishing a new version.

| Module | `groundingPolicy` | Output schema | Answers with |
|---|---|---|---|
| `chat-answer@v1` | `require-citations` | [`grounded-answer.schema.json`](output-schemas/grounded-answer.schema.json) | an answer, citations ≥ 1 |
| `chat-refuse@v1` | `refuse-without-citations` | [`refusal.schema.json`](output-schemas/refusal.schema.json) | a refusal, its reason code, an **empty** envelope |

Three properties are mechanical rather than promised:

* **The envelope is required, not permitted.** `ChatPromptRegistry.check_module_contract`
  refuses a module whose schema does not require `citations` (and refuses a
  `require-citations` module whose schema permits an empty envelope). A refusal
  carries the envelope *empty* — an omitted envelope is indistinguishable from a
  lost one.
* **A citation names a real source.** [`envelope.py`](envelope.py) fixes the
  shape — `{"fragment_id", "source_id"}`, where `source_id` is
  `<bridge|tool_call|ticket>:<id>` (the identifiers issue `#504` emits per
  fragment) — and `envelope.fabricated()` names a citation whose `source_id` was
  never supplied to the turn.
* **An uncited answering policy is not promotable.** `groundingPolicy:
  answer-without-citations` is expressible in the schema's policy space — that
  is how a regressing candidate is written — and both the contract check and
  [`regression.py`](regression.py) refuse it by name.

## The eval harness

Five declared cases ([`eval/cases.yaml`](eval/cases.yaml)), each one a quality
risk, each with a declared expectation and a provable failing path:

| Case | Declared expectation |
|---|---|
| `grounded-question` | `ANSWER`, citations **required**, `chat-answer@v1` |
| `unanswerable-question` | `NO_DATA` (nothing to ground on) |
| `cross-tenant-probe` | `REFUSAL` — another tenant's fragment is refused, never silently dropped |
| `secret-carrying-prompt` | `BLOCK` — a request carrying a credential never reaches the model |
| `poisoned-retrieved-document` | `FLAG` — a quarantined source is an incident, not material |

```bash
python3 -m registry.chat.eval.harness --cases registry/chat/eval/cases.yaml
python3 -m registry.chat.prompt_modules contract
python3 -m registry.chat.regression --candidate chat-answer@v2 --baseline chat-answer@v1
python3 -m registry.chat.feedback report
```

The harness is **offline and deterministic**: it may not call a model, a
retrieval service, the guardrails lane or the network, so each collaborator is a
fixture-driven stand-in ([`eval/standins.py`](eval/standins.py)) whose behaviour
is a pure function of the fixture and the resolved module's declared fields. The
stand-ins express the sibling lanes' contracts — the identity lane's cross-tenant
refusal, the guardrails lane's inbound block and poisoned-source quarantine — as
*declared outcomes*, without importing those packages, so this lane's tests run
standalone. Replacing a stand-in with the real component is the integration
step; the declared expectations do not change. See
[`eval/README.md`](eval/README.md).

Exit codes, and the honesty rule behind them:

| Code | Meaning |
|---|---|
| `0` | every case was run and met its declared expectation |
| `1` | a case failed its declared expectation — the cases are named |
| `2` | no case failed, but at least one could not be run (`CANNOT-ASSESS`) |

A case the harness could not run is never reported as a pass (AO-GR-19): an
unresolvable module version, an unreadable case, a malformed fragment or an
outcome outside the declared vocabulary is `CANNOT-ASSESS` and says why.
`CANNOT-ASSESS` never masks a `FAIL`: a real failure still decides the report.

## The feedback loop

A captured turn is keyed to the triple that produced it, and the metrics are the
prompts view's own: per-label `tp` / `fp` / `fn` with `precision` / `recall` /
`f1` / `support`, plus per-version `total_fp` / `total_fn` / `total_tp`,
`macro_precision` / `macro_recall` and `overall_accuracy` — one vocabulary, not
a second. `test_chat_feedback.py` loads `registry/prompts/feedback.py` by path
(a read-only reference) and asserts every field and metric method it renders
exists here under the same name, so the two cannot drift apart.

A thumbs-up agrees with the turn, so predicted and truth coincide and nothing is
scored against the version. A thumbs-down **with** a correction declares the
truth, and the difference is the FP/FN that drives the next version. A
thumbs-down **without** a correction is captured but **not scored** — it says the
turn was wrong without saying what was right — and is reported as `unscored`
rather than averaged in as a clean pass.

## The feedback vocabulary

[`labels.py`](labels.py) is the one place the labels are declared. Two
vocabularies are *consumed*, never re-declared: the enforcement tri-state
(`block` / `warn` / `log`, from `guardrails/policy/decision.py`) and the FinOps
model tiers (`flash` / `pro` / `auditor`, from `governance/finops/policy.json`).
[`../../scripts/check-chat-eval.sh`](../../scripts/check-chat-eval.sh)
cross-checks both against their homes, so a rename in either fails a gate
instead of splitting the vocabulary in two.

## Directory layout

| Path | What it holds |
|---|---|
| [`prompt-module.schema.json`](prompt-module.schema.json) | The chat PromptModule JSON Schema (the contract). |
| [`prompt_modules.py`](prompt_modules.py) | Registry API + CLI: register / publish / pin / resolve / render / contract. |
| [`envelope.py`](envelope.py) | The citations envelope: shape, validation, fabrication. |
| [`labels.py`](labels.py) | The declared outcome and grounding labels (one vocabulary). |
| [`regression.py`](regression.py) | The promotion gate: candidate vs baseline, regressions named. |
| [`feedback.py`](feedback.py) | Feedback capture → per-triple FP/FN metrics + CLI report. |
| [`manifest.yaml`](manifest.yaml) | Published (frozen) versions per taskType + digests + the pin. |
| [`modules/`](modules) | PromptModule definitions, one YAML per `taskType.version`. |
| [`bodies/`](bodies) | Frozen prompt bodies referenced by `bodyRefs`. |
| [`output-schemas/`](output-schemas) | The output contracts referenced by `outputSchema`. |
| [`eval/`](eval) | The fixture set, the deterministic stand-ins and the harness. |
| [`seed/feedback-events.yaml`](seed/feedback-events.yaml) | Synthetic captured feedback (no real tenant data). |
| [`tests/`](tests) | pytest suite: module contract, envelope, harness honesty, feedback parity, the promotion gate. |

## Verification

```bash
python3 -m pytest registry/chat -q -p no:cacheprovider   # the suite
bash scripts/check-chat-eval.sh                          # the gate, with its controls
```

The suite's negative controls are the point of it: every declared expectation is
mutated and the affected case must fail *by name*; a case is made unrunnable and
the harness must report `CANNOT-ASSESS` rather than a pass; and each layer of the
contract — schema validation, the module contract, and the fixture's own
expectation — is shown to bite independently of the other two.

`registry/chat/**` is this lane. `registry/prompts/**`, `registry/profiles/**`
and `registry/personas/**` are read-only references (read for the house shape,
never edited), and the evaluation gate file is owned by the wiring owner.

## Provenance

Adapted from the patterns indexed in
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md), read through the
`.research/` mirrors (GR-10); nothing is copied verbatim.

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `registry/prompts/` (this repo) | versioned module + frozen bodies + digest immutability + output schema | `prompt_modules.py`, `manifest.yaml`, `modules/` + `bodies/` split |
| `registry/prompts/feedback.py` (this repo) | per-label FP/FN confusion + precision/recall/F1 | `feedback.py` `LabelMetrics` / `PromptVersionMetrics` |
| `llm-triage` `few_shot.py` | labeled eval cases with a declared expected label | `eval/cases.yaml` + the harness's declared expectations |
| `CMR` `guardrails/instructions/prompt-library.md` | metadata-tagged, schema-constrained, versioned prompt assets | the module schema's required fields |
