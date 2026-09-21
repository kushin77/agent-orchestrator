"""Nous Research provider adapter (issue #1559) - OpenAI-compatible chat.

Nous Research's inference API is OpenAI-compatible: ``POST {base}/chat/completions``
with the account API key as a bearer token. The published contract
(``https://portal.nousresearch.com/api-docs``, OpenAPI mirror at
``https://portal.nousresearch.com/api/openapi``) declares

- base URL ``https://inference-api.nousresearch.com/v1``, usable "with
  OpenAI-compatible clients and libraries";
- authentication via a portal API key set as a bearer token in the
  ``Authorization`` header, billed against the account's credits (or, in beta,
  via x402 pay-per-request with an ``X-PAYMENT`` header - not implemented here);
- ``POST /chat/completions`` and ``POST /completions``;
- a live model listing at ``GET /v1/models`` - HTTP 200 **unauthenticated**,
  **402 models**, each with a ``pricing.prompt`` / ``pricing.completion``
  per-token rate (measured 2026-09-20).

The docs also list three ``Hermes-4.x`` chat models as available. **They are
not usable, and are not what this adapter routes to.** Measured against the live
endpoint on 2026-09-20, unauthenticated:

- ``Hermes-4-70B`` -> 404 "This model has been retired."
- ``Hermes-4-405B`` -> 404 "This model has been retired."
- ``Hermes-4.3-36B`` -> 404 "not found ... does not exist in our configuration
  or OpenRouter catalog"

**And the ``/v1/models`` listing is not the chat namespace either.** Probing the
chat path directly is what settles it, because the endpoint's own signals are
unambiguous: it answers HTTP **402** (the x402 payment-required flow) for a model
id it *accepts*, **400** "Unknown model" for one it does not, and **401** for a
bad key. Of the 402 listed models, the chat path refuses most of them - so a tier
map taken from either the docs or the listing would ship dead ids, which is the
precise defect this issue (#1559) exists to fix. The four ids in ``config.py``
are the ones that answered 402, one per rung, ascending by the endpoint's own
published rates.

The FinOps credit meter for those ids lives in
``gateway/finops/provider-credits.yaml`` (plan ceilings plus top-up tracking;
the contract exposes no balance endpoint, so the meter says so).

Because the wire shape is the OpenAI ``chat/completions`` protocol, the request
builder and the response parser are inherited from ``OpenAICompatProvider`` -
the same shared base the ``openai``, ``deepseek``, ``paperclip`` and ``copilot``
adapters use. This adapter is therefore an OpenAI-compatible subclass, not an
``OllamaProvider`` one (the ``/api/chat`` Ollama wire shape is the *local*
hermes hop's protocol, a different target entirely).

**This is not the ``hermes`` provider, deliberately.** ``hermes`` is the
keyless, Ollama-compatible local inference hop: ``integrations/hermes/``
declares it (ADR-0012) as the *namesake excluded* from the hermes-agents
routing service, ``telemetry/metering/rate_cards/hermes.yaml`` prices it
``local: true`` at $0, and EPIC #253's frozen ``hermes -> hermes/ollama`` map
makes local Ollama its terminal fallback. ``nous`` is the opposite: a *billed*
cloud target behind an API key. Re-pointing the ``hermes`` id at this API
would silently falsify all of those declarations (none of them derives its
hermes facts from ``config.py``, so no gate would catch it) - a distinct
provider id is what keeps every declaration true, and is what makes this
provider's own catalog module addressable. ``copilot`` is a distinct id for
exactly the same reason (see ``config.py``).

Credentials never appear in code, logs or git: the API key is read through the
gateway's ``ApiKeyVault`` (``AO_VAULT_KEY``-encrypted at rest), sourced from
Vault ``secret/shared-services/nous`` and mirrored to GSM. An absent key means
no ``Authorization`` header is sent at all, so the call fails at the provider
with a 401 rather than silently succeeding.

The GSM mirror needs a declared landing spot for the *default* (non-tenant)
key (issue #1748): ``NOUS_API_KEY_ENV`` names the environment variable the
Cloud Run revision is given via Terraform ``secret_key_ref``
(``infra/terraform/provider-credentials.json`` -> ``infra/terraform/main.tf``,
gated on the existing ``enable_hermes`` flag, default OFF). Reading it here
mirrors the repo's one existing convention for secret material delivered as
an env var: ``gateway/providers/vault.py::load_master_key`` reads
``AO_VAULT_KEY`` with the same ``os.environ.get(env, "")`` shape. No key set
means ``default_api_key()`` returns ``None`` and the provider fails closed at
the API (401), never a literal fallback.

---knowledge---
module_id: gateway.providers.nous
system: gateway
app: providers
solution_class: enterprise
patterns: [openai-compatible, key-from-environment, published-contract]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [NousProvider, default_api_key]
invariants: "the adapter is OpenAI-compatible and its calls are billed against the account's prepaid credits"
gotchas: "the upstream x402 pay-per-request path is documented but deliberately not implemented here"
related: ["#1559"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import os

from providers.base import OpenAICompatProvider

#: Env var the Cloud Run revision is given, via Terraform secret_key_ref,
#: when `enable_hermes` is on (infra/terraform/provider-credentials.json).
NOUS_API_KEY_ENV = "NOUS_API_KEY"


def default_api_key(env: str | None = None) -> str | None:
    """The default (non-tenant) Nous API key from the environment, if set.

    Returns ``None`` when unset or empty — fail closed, never a literal
    fallback (GR-6). The per-tenant path stays ``ApiKeyVault``; this is only
    the landing spot for the IaC-projected default key (issue #1748).
    """
    value = os.environ.get(env or NOUS_API_KEY_ENV, "")
    return value or None


class NousProvider(OpenAICompatProvider):
    """Nous Research inference-API chat adapter (OpenAI-compatible)."""

    name = "nous"
