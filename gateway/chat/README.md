# `gateway/chat` — the conversational serving surface (issue #503, ADR-0023)

The control plane's **OpenAI- and Ollama-compatible chat endpoints**. This is the
*mount* `gateway/proxy/handler.py` was written for: its dispatch core is
transport-free by design, and the phase-7 REST surface the docstring names is
here.

A conversational turn is **not a new kind of thing** — it is one dispatch through
`gateway/proxy`, carrying a friendlier envelope. Routing, tier choice, context
caps, budgets, fallback, DLP, metering, the ledger and the audit record are
inherited because this package did **not** build a second path to a model.

## Pointing a client at it

Any OpenAI-compatible client works unchanged. The `model` field carries a
**tier**, not a provider model — the FinOps chooser resolves the model behind it
(ADR-0023 §4).

```bash
# OpenAI-compatible (the connection the portal and OpenWebUI use)
curl -sS https://<surface>/v1/chat/completions \
  -H "Authorization: Bearer $AO_CHAT_CREDENTIAL" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "MED",
        "messages": [{"role": "user", "content": "What happened in OPS-1187?"}],
        "stream": true,
        "grounding": {
          "prefix": "<assembled grounding prefix>",
          "fragments": [{"source_id": "ticket:OPS-1187", "text": "…", "kind": "ticket"}]
        }
      }'

# the advertisement (also the authority on what `model` may be)
curl -sS https://<surface>/v1/models

# Ollama-shaped clients: point their base URL at the same host
curl -sS https://<surface>/api/chat \
  -H "Authorization: Bearer $AO_CHAT_CREDENTIAL" \
  -d '{"model": "MED", "messages": [{"role": "user", "content": "…"}]}'
```

| Endpoint | Contract | Streaming |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI: `model`, `messages`, `stream`, `tools` | `data: {chunk}` frames ended by `data: [DONE]` |
| `POST /api/chat` | Ollama: `model`, `messages`, `options` | newline-delimited JSON, final frame `"done": true` |
| `GET /v1/models` | OpenAI model list | — |

The **`ao` extension** rides inside the compatible envelope (additive: an
OpenAI-only client ignores it). It is where the platform's own facts live:
`turnId`, `container`, `promptModule`, `tier{requested,resolved,resolvedModel,
claimHonoured}`, `degraded{fromTier,toTier}`, `grounding{state,admitted,
quarantined}`, `citations{sources,fragments,envelope}`, `usage{estimatedCostUsd,
latencyMs}`, `budget`, `attribution`, `verdicts`, `inbound`, `claim`,
`dispatch{record,stages,toolsSupplied,declaresTools}`. `grounding.state` is
`OK` only when an admitted fragment backed a citation; otherwise `NO_DATA`
(AO-GR-19: an honest empty, never an empty success).

`GET /v1/models` is **derived**, never hand-written: one entry per
`gateway/catalog/modules/*/module.json`, plus the tier ladder read from
`gateway/providers/contract.py`, with routability resolved from
`gateway/proxy/config/routing.yaml`. A module added to the catalog appears with
no edit here; a provider module is advertised with `selectable: false` (asking
for one as `model` is refused — `model_not_selectable`).

## Flags and posture

* `surfaces.chat` in `infra/feature-flags/registry.yaml` ships **off**. While it
  is off the surface is **absent**: `404 feature_disabled` in the compatible
  error shape, to an anonymous probe *and* to a valid credential, because the
  flag is checked **before AuthN**.
* `services.chat` + `infra/terraform/variables.tf`'s `enable_chat` (both
  `off`/`false`) are the paired declarations the feature-flags gate requires —
  a dedicated flag, so promoting (or killing) chat does not promote (or kill)
  the whole gateway.
* **Identity comes from the verified credential** (`identity/chat`), never from
  the body. `tenantId`/`tenant`, `role`, `api_key`, `budget*` in a request are
  recorded as *claims*; a foreign tenant claim is refused (`tenant_mismatch`),
  and the rest are ignored.

### Refusals (all OpenAI-compatible)

| When | Status | `error.code` |
|---|---|---|
| flag off (before AuthN) | 404 | `feature_disabled` |
| no credential / bad credential | 401 | `credential_required` / `invalid_api_key` |
| foreign tenant or conversation | 403 | `tenant_mismatch` |
| malformed body / unreadable grounding | 400 | `invalid_request` / `invalid_grounding` |
| unknown or non-selectable model | 404 / 400 | `model_not_found` / `model_not_selectable` |
| guardrails abort (retrieval, DLP, policy) | 403 | `guardrail_blocked` (rule ids only) |
| budget rail (kill switch, quota, cap) | 429 | `budget_blocked` (attributed) |
| dispatch not served | 403/422/429/502/503 | `capability_denied`, `cannot_assess`, `output_refused`, `upstream_failed`, `no_healthy_route`, … |
| answer failed inbound re-validation | 422 | `ungrounded_response` |

Refusals may carry an `ao` sibling with the evidence (the call record, the
budget verdict, the attribution, the guard verdicts) — never the payload that
tripped a guard.

## What it composes (and what it refuses to own)

| Consumed | For |
|---|---|
| `gateway/proxy` | the one model path: `dispatch`/`dispatch_stream`, routing, caps, budgets, fallback, the `GatewayCallRecord` emitted to the audit **and** metering sinks on every dispatch |
| `identity/chat` | the scoped `(tenant, agent, conversation)` credential (`verify_chat_credential`, revocation always consulted) and the conversation isolation |
| `telemetry/chat` | `TurnBudgetGuard` **before** the model call (kill switch first), `TurnAttributor` after (one metering row + one ledger event) |
| `guardrails/chat` | retrieval-injection defense, DLP egress (abort on block), inbound re-validation |
| `gateway/mcp` | the grounding hand-off: assembled prefix + tool declarations pass through unchanged |
| `registry/chat` | the published prompt modules (`chat-answer` when grounded, `chat-refuse` when not) |
| `engine/memory` | the conversation container `session:<tenant>:<agent>:<conversation>` (via `identity/chat`) |

It **declares no tool** (`dispatch.declaresTools` is always `false`), **assembles
no grounding**, and **owns no model, price or budget figure**.

The router it uses is the proxy's own policy, loaded through the proxy's own
loader and extended with the two routes this surface's prompt modules need
(`config/chat-routes.yaml`, both mapped onto the existing `research` FinOps task
class). A collision with the proxy's policy is a refusal, never an override.

## Wiring it

```python
from gateway.chat.wiring import build_surface

surface = build_surface(
    registry_path=None,                       # infra/feature-flags/registry.yaml
    signing_key=os.environ["AO_CHAT_SIGNING_KEY"].encode(),   # never a default
    revocation_store=revocation_store,        # identity/sso store
    ledger=ledger,                            # telemetry/ledger
    usage_store=usage_store,                  # telemetry/metering
)
body = surface.completions(request_body, token=bearer_token)
frames = surface.completions_stream(request_body, token=bearer_token)  # SSE text
```

Everything is offline by construction: provider traffic goes through the
proxy's scriptable transport rig, and the prompt modules, catalogs and routing
policy are read from the committed seeds. `build_offline_surface` is the same
function under its honest name.

## Known cross-lane item (reported, not fixed here)

`gateway/mcp` identifies a read fragment as `family:target@revision` (for
example `board:#1@c6e96d5981d01028`), while `registry/chat`'s
`grounded-answer` schema requires a citation `source_id` matching
`^(bridge|tool_call|ticket):…`. A turn grounded on a family whose ids carry the
first shape cannot produce a schema-valid citation, and this surface **fails
closed** (`cannot_assess`) rather than rewriting an id it was handed — see
`tests/test_grounding_handoff.py::test_the_mcp_assembler_prefix_passes_through`.
Reconciling the two vocabularies belongs to the two owning lanes.

## Tests

```bash
python3 -m pytest gateway/chat -q -p no:cacheprovider
bash scripts/check-chat-surface.sh
```
