"""hermes integration adapter (issue #942, ADR-0012).

Hermes is the fleet's **orchestration** peer (ADR-0012): the vendored
`hermes-agents` module owns agent routing / capability registry / escalation /
model-tiering. This package is the thin, **read-only projection** of that
declared surface as it exists in this repo — the persona card, the profile
seed, the FinOps tier table and the gateway catalog row — onto one canonical
document, plus a transport-seamed client over the service contract it declares
(Flask on port 9501: ``/health``, ``/api/capabilities``, ``/api/router``,
``/api/tiering``).

It maps the policy; it does not couple the runtime (ADR-0012). It is **not** an
embedded router, and it does **not** bind the gateway namesake
``gateway/providers/hermes.py`` — that is an Ollama-compatible inference
endpoint for the Hermes-3 LLM, explicitly excluded here by name.

The package is stdlib-only: the mapping reads its YAML sources with the small
in-repo subset loader it shares with ``integrations/paperclip/``
(``integrations/_seam/``, issue #1208) and the offline transport replays canned
responses, so the gate and the tests never need a third-party dependency or the
network.
"""

__all__ = ["audit", "client", "mapping", "model", "policy"]
