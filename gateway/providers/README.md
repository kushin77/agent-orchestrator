# gateway/providers — multi-provider client adapters

> Owner lane: **gateway** (issue #15, work item 11, phase 2). Parent: EPIC-00
> (issue #4). Doctrine: [`../../AGENTS.md`](../../AGENTS.md),
> [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`../../docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).
> Cannibalization index: [`../../docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).

This tree is the **multi-provider client adapter layer** of the Model
Gateways pillar (pillar 2, phase 2): a clean, typed, retry-able, measurable
provider interface for **Claude (Anthropic), DeepSeek, OpenAI (Copilot/GPT),
Gemini and local Ollama** — swappable without touching orchestration. The
gateway proxy (issue #16) and later phases consume these adapters.

This module is the **contract-freeze boundary** for phase 2 (per
`docs/EXECUTION-PLAN.md`): field names here are frozen; later lanes consume
them, they do not change them. In particular the tier vocabulary
(`LOW/MED/HIGH/MAX`) is **consumed** from the issue-#9 AgentProfile catalog
(`registry/profiles/catalog.yaml` `tiers`) and output schemas are JSON Schema
as declared by the issue-#13 prompt modules (`outputSchema`) — this lane does
not redefine either.

## What this layer is (30 seconds)

A caller (later: the gateway proxy) asks the registry for one model call:

```python
result = registry.chat(
    messages,            # list of {role, content} or ChatMessage
    schema,              # JSON Schema for the typed output (or None)
    tenant_id="acme",
    agent_id="orchestrator-1",
    logical_key="MED",   # an issue-#9 tier, or a tenant task key
)
# result.content           -> schema-validated object (typed output)
# result.model             -> model actually used
# result.usage.tokens      -> input + output tokens
# result.latency_ms        -> wall-clock latency of the call
# result.provider/tenant_id/agent_id/logical_key  -> the call stamp
```

The registry resolves `logical_key` → an exact `(provider, model)` route
(per-tenant overrides allowed), pulls the tenant's API key from the encrypted
vault, runs the adapter under retry/backoff + a circuit breaker, degrades to
the provider's fallback chain when it is unavailable, validates the typed
output against the output schema (fail closed) and fires one stamped
`ModelCallEvent` to the metering and audit hooks.

## Tree layout

```text
gateway/providers/
├── README.md                   # this file — the contract + config guide
├── __init__.py                 # package public surface (import as ``providers``)
├── contract.py                 # ModelProvider ABC + value objects (contract-freeze)
├── errors.py                   # provider/vault exception taxonomy
├── config.py                   # ProviderConfig + per-tenant overrides (+ YAML loader)
├── schema.py                   # output-schema validation (JSON Schema, fail closed)
├── transport.py                # pluggable HTTP transport + offline doubles
├── resilience.py               # RetryPolicy / CircuitBreaker / manager
├── events.py                   # ModelCallEvent + EventRouter (metering + audit)
├── vault.py                    # per-tenant API-key vault (encrypted at rest)
├── registry.py                 # ProviderRegistry + resilient ProviderClient
├── base.py                     # HttpModelProvider + OpenAI-compatible base
├── anthropic.py                # Claude adapter
├── deepseek.py                 # DeepSeek adapter
├── openai.py                   # OpenAI/Copilot/GPT adapter
├── gemini.py                   # Gemini adapter
├── ollama.py                   # local Ollama adapter
├── example-tenant-overrides.yaml   # documented per-tenant mapping example
└── tests/                      # offline pytest suite (97 tests)
```

## The `ModelProvider` contract

`contract.ModelProvider` is the abstract surface every adapter implements:

```python
chat(messages, schema, options, context) -> ChatResult
```

where the typed result carries **`{content, model_used, usage{tokens},
latency_ms}`** plus the call stamp (`provider`, `tenant_id`, `agent_id`,
`logical_key`):

| Result field | Meaning |
|---|---|
| `content` | The schema-validated structured object (when `schema` given) or the plain text |
| `model` | The model id the provider reported it actually used |
| `usage.input_tokens` / `usage.output_tokens` / `usage.tokens` | Token usage |
| `latency_ms` | Wall-clock latency of the provider round-trip |
| `provider`/`tenant_id`/`agent_id`/`logical_key` | The call stamp |

**Typed output, fail closed.** Each adapter extracts the content text and,
when an output schema is supplied, validates it against the JSON Schema
(`schema.py`, via `jsonschema`). Content that is not parseable JSON or that
violates the schema raises `OutputValidationError` — there is **no silent
pass-through of invalid output**. Malformed caller schemas raise
`SchemaDefinitionError` up front.

**Transport injection, offline by default.** Adapters never open sockets; the
HTTP transport is injected (`transport.py`). Tests use
`RecordingTransport`/`FailingTransport`; production uses
`StdlibHttpTransport` (stdlib only — no `requests` dependency). No test makes
a network call.

**Resilience is not the adapter's job.** Retry/backoff and the circuit
breaker live in `resilience.py` and are applied per provider by
`ProviderClient` (registry.py) — mirroring the harvested ollama resilient
client and defragsuite gateway. Only *transient* failures
(`ProviderUnavailableError`/timeout) are retried and trip the breaker;
non-transient failures (bad key/request, invalid output) fail immediately.

## Provider adapters

| Provider | Adapter | Protocol | Auth | Tier → model (default) |
|---|---|---|---|---|
| Claude | `anthropic.py` | `POST /v1/messages` | `x-api-key` | LOW→`claude-haiku-4-5`, MED/HIGH→`claude-sonnet-4-5`, MAX→`claude-opus-4-5` |
| DeepSeek | `deepseek.py` | OpenAI-compatible `chat/completions` | Bearer | LOW/MED→`deepseek-chat`, HIGH/MAX→`deepseek-reasoner` |
| OpenAI / Copilot / GPT | `openai.py` | OpenAI-compatible `chat/completions` | Bearer | LOW/MED→`gpt-4o-mini`, HIGH/MAX→`gpt-4o` |
| Gemini | `gemini.py` | `models/{model}:generateContent` | `x-goog-api-key` | LOW/MED→`gemini-2.5-flash`, HIGH/MAX→`gemini-2.5-pro` |
| Ollama (local) | `ollama.py` | `/api/chat` | none (keyless) | LOW/MED→`llama3.2`, HIGH/MAX→`qwen2.5` |

Every adapter maps `system` messages per its wire protocol (Anthropic
top-level `system`, Gemini `systemInstruction`, inline system role for the
OpenAI-compatible and Ollama protocols), maps assistant roles correctly
(Gemini `model`), and parses usage from the provider's own usage fields. The
`openai` and `deepseek` adapters share the OpenAI-compatible base
(`base.py`); the Copilot/enterprise GPT case is served by overriding the
`base_url` on the `openai` `ProviderConfig`.

## Provider registry + routing

`registry.ProviderRegistry` owns:

- **Provider configs** — `config.default_provider_configs()` returns the five
  platform defaults (`ProviderConfig`: base URL, tier→model map,
  supported-model set, timeout, retry policy, breaker settings, fallback
  chain, `requires_key`). Unknown models are rejected before any request
  (fail closed).
- **Routing** — `resolve_route(tenant_id, logical_key)` maps a logical key
  (a `LOW/MED/HIGH/MAX` tier, or a tenant task key) to an exact
  `(provider, model)`. Default tier routes target the platform default
  provider (`deepseek`); an unknown logical key is rejected.
- **Per-tenant overrides (criterion 5)** — a tenant remaps any logical key to
  `"provider"` (that provider's default model for the tier) or
  `"provider/model"` (a pinned model). Set them in code
  (`registry.set_tenant_mapping`) or load them from YAML
  (`registry.load_tenant_overrides`) — see
  [`example-tenant-overrides.yaml`](example-tenant-overrides.yaml):

```yaml
tenants:
  - tenantId: acme
    mappings:
      LOW: anthropic                      # use anthropic's LOW-tier model
      MAX: anthropic/claude-opus-4-5      # pin an exact model
      summarize: ollama/llama3.2          # a tenant task key
```

- **Resilient clients** — one `ProviderClient` per `(tenant, provider)`
  (adapter + credentials + circuit breaker + retry policy) is built lazily
  and reused. Each provider call runs under
  `resilience.call_with_retries` (exponential backoff; CLOSED → OPEN →
  HALF_OPEN circuit breaker, ollama semantics) and **graceful degradation**:
  when the routed provider is unavailable, the registry walks the provider's
  `fallback` chain — cloud → local Ollama by default (the defragsuite /
  gov-ai-scout cloud→local pattern). Each fallback resolves its *own* model
  for the same logical key.
- **Fail closed on credentials** — a cloud provider with no API key for the
  tenant refuses to call unauthenticated (`ProviderConfigurationError`).

## Per-tenant API-key vault (encrypted at rest)

`vault.ApiKeyVault` stores tenant+provider API keys **encrypted at rest**:

- Master key from the environment variable `AO_VAULT_KEY` (a Fernet key) —
  never a literal in code, never committed.
- Encryption via `cryptography` (Fernet = AES-128-CBC + HMAC-SHA256,
  authenticated). If the library is unavailable the vault **refuses to
  operate** (`VaultUnavailableError`) rather than downgrade to plaintext or a
  hand-rolled cipher — honest fail-closed, never security theater.
- The persisted vault file holds ciphertext tokens + tenant/provider metadata
  only. Keys never appear in logs, exceptions, `repr`/`str`, events or chat
  options (`test_vault.py` asserts the no-plaintext-in-logs guarantee).

Operator bootstrap:

```bash
export AO_VAULT_KEY="$(python3 -c 'from providers.vault import new_master_key; print(new_master_key())')"
```

> The command above is illustrative — generate the key in your secret manager
> and inject it via env; never paste a real key into code, files or logs.

## Metering + audit hooks (criterion 4)

Every provider call emits one stamped `ModelCallEvent` (provider, model,
tenant, agent, logical key, status, usage, latency, attempts, error) through
the `EventRouter`:

```python
registry.add_metering_hook(handler)   # token/latency/cost metering (phase 5)
registry.add_audit_hook(handler)      # full-trace audit trail
```

Hooks that raise propagate (fail closed): audit/metering records are never
silently lost. Events carry no key material by construction.

## Importing the package

`gateway/` has no `__init__.py` yet (a later gateway-phase lane adds one), so
the package is importable as `providers` when `gateway/` is on `sys.path` —
the same convention as `identity/rbac` (its `tests/conftest.py` inserts
`gateway/`). A downstream lane can do the same, or add `gateway/__init__.py`
and import `gateway.providers`.

## Verification

```bash
python3 -m pytest gateway/providers/tests -q     # 97 offline tests
make verify                                       # repo gate of record (stays green)
```

Test coverage (all offline): interface contract; typed-output validation
(invalid output fails closed on every provider); retry-then-fail; circuit
breaker open/close/half-open; vault encrypt/decrypt + no-plaintext-in-logs;
per-tenant model override; metering/audit hooks fired per call; graceful
degradation cloud→local; transport doubles.

## Provenance

Adapted (not copied) from the sources indexed in
[`docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md), each read through
the `.research/` read-only mirrors (GR-10):

| Source | Pattern adapted | Where it landed |
|---|---|---|
| `gov-ai-scout` `ai-provider.ts` | per-task model registry + fallback + validated typed output | registry routing + `schema.py` fail-closed validation |
| `llm-triage` `classifier.py` | provider ABC + retry + result with model_used/tokens/latency | `contract.py` `ChatResult` + `ModelProvider` |
| `ollama` `resilient_ollama_client.py` + `circuit_breaker.py` | circuit breaker (CLOSED/OPEN/HALF_OPEN) + resilient client | `resilience.py` + `registry.ProviderClient` |
| `gmail-agent` `claude.ts` | model tiers (sonnet/opus/haiku) + retry + output parse | anthropic tier→model map + fail-closed parse |
| `capital-underwriting` `gemini.ts` + `aiDraft.ts` | metered Gemini calls, per-deployment model/base_url config | `gemini.py` + `ProviderConfig.base_url` override |
| `defragsuite` `pkg/defrag-ai/modal/client.go` | gateway client with circuit breaker + cloud→local fallback | `registry.chat` fallback chain (graceful degradation) |
