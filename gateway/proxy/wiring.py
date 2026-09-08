"""Composition wiring for the gateway proxy (issue #16).

The dispatch core is seam-injected and standalone-testable; this module is the
composition root that wires the REAL merged sibling modules into a
``ModelGateway`` for offline demos and integration tests:

- personas (issue #11) + profile mapping (issue #9)  -> agent resolver
- prompt library (issue #13)                          -> task resolver
- FinOps chooser (issue #17)                          -> tier chooser
- provider registry (issue #15) over offline doubles -> model backend
- limits facade (issue #19)                           -> cost/capacity guard

Everything here stays OFFLINE: providers are driven through a scriptable
transport rig (canned responses, no sockets) and the prompt/persona registries
are read-only consumers of the committed seed data.  No file in another lane
is modified — the merged modules are consumed through ``sys.path``/importlib
and their own on-disk seeds.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from proxy import contract
from proxy.backend import (
    BackendConfigurationError,
    BackendOutputInvalidError,
    BackendResult,
    BackendUnavailableError,
)
from proxy.gateway import ModelGateway
from proxy.model import AgentView, Message, RouteCandidate, TaskView, TierChoice
from proxy.resolver import HealthSignal
from proxy.sinks import CallRecordSink, ListCallRecordSink
from proxy import schema as proxy_schema

# gateway/proxy/wiring.py -> gateway/proxy -> gateway -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATEWAY_DIR = REPO_ROOT / "gateway"
FIN_OPS_DIR = GATEWAY_DIR / "finops"
PROMPTS_DIR = REPO_ROOT / "registry" / "prompts"
PERSONAS_DIR = REPO_ROOT / "registry" / "personas"

# --------------------------------------------------------------------------- #
# sys.path + file-path module loading (avoid top-level module-name collisions
# between prompts/registry.py and personas/registry.py, both literally named
# ``registry``)
# --------------------------------------------------------------------------- #
_sys_path_setup_done = False


def ensure_paths() -> None:
    """Idempotently make the finops plain modules importable."""
    global _sys_path_setup_done
    if _sys_path_setup_done:
        return
    for path in (GATEWAY_DIR, FIN_OPS_DIR):
        if str(path) not in sys.path:
            sys.path.append(str(path))
    _sys_path_setup_done = True


def _load_module(module_name: str, path: Path):
    """Load a standalone module from a file path under a unique module name."""
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_prompts() -> Any:
    """The real registry/prompts registry module (read-only seeds)."""
    return _load_module("ao_proxy_promptlib", PROMPTS_DIR / "registry.py")


def load_personas() -> Any:
    """The real registry/personas registry module (read-only seeds)."""
    return _load_module("ao_proxy_personas", PERSONAS_DIR / "registry.py")


def load_persona_mapping() -> Any:
    """The real registry/personas mapping module (persona -> profile)."""
    return _load_module("ao_proxy_persona_mapping", PERSONAS_DIR / "mapping.py")


# --------------------------------------------------------------------------- #
# Real adapters (consume the merged contracts)
# --------------------------------------------------------------------------- #
class PersonaAgentResolver:
    """Agent resolver over the issue-#9/#11 contracts (personas + mapping)."""

    def __init__(self, personas, mapping) -> None:
        self.personas = personas
        self.mapping = mapping

    def resolve(self, tenant_id: str, agent_id: str) -> AgentView:
        try:
            card = self.personas.get(tenant_id, agent_id, fallback_to_platform=True)
        except Exception as exc:  # noqa: BLE001 - UnknownPersonaError etc.
            raise contract.AgentResolutionError(str(exc)) from exc
        try:
            profile = self.mapping.materialize_profile(card, tenant=tenant_id)
        except Exception as exc:  # noqa: BLE001 - ProfileMaterializationError
            raise contract.AgentResolutionError(str(exc)) from exc
        return AgentView(
            agent_id=agent_id,
            tenant_id=tenant_id,
            profile_id=profile["id"],
            persona_id=card["id"],
            owner=profile["owner"],
            capability_set=frozenset(profile["capabilitySet"]),
            tool_allowlist=frozenset(profile["toolAllowlist"]),
            constraint_set=frozenset(profile["constraintSet"]),
            default_model_tier=profile["defaultModelTier"],
            guardrail_policy_ref=profile["guardrailPolicyRef"],
        )


class PromptTaskResolver:
    """Task resolver over the issue-#13 prompt-library contract."""

    def __init__(self, prompts) -> None:
        self.prompts = prompts

    def resolve(self, task_type: str, variables: Mapping[str, Any] | None = None) -> TaskView:
        try:
            resolved = self.prompts.resolve(task_type)
            rendered = self.prompts.render_prompt(task_type, dict(variables or {}))
        except Exception as exc:  # noqa: BLE001 - UnknownTaskType/RenderError
            raise contract.TaskResolutionError(str(exc)) from exc
        bodies = rendered.get("bodies") or {}
        # system body first, then user/assistant bodies in declaration order
        ordered = []
        for role in ("system", "user", "assistant", "examples"):
            if bodies.get(role):
                ordered.append(Message(role=role, content=str(bodies[role])))
        output_schema = None
        schema_src = rendered.get("outputSchema") or resolved.output_schema
        if schema_src:
            output_schema = proxy_schema.load_output_schema(str(schema_src))
        return TaskView(
            task_type=task_type,
            version=resolved.version,
            prompt_id=resolved.prompt_id,
            model_tier_hint=resolved.model_tier_hint,
            messages=tuple(ordered),
            output_schema=output_schema,
            parameters=dict(resolved.module.get("parameters", {})),
        )


class FinOpsChooserAdapter:
    """Chooser seam over the issue-#17 FinOps model chooser."""

    def __init__(self, chooser) -> None:
        self.chooser = chooser

    def choose(
        self,
        task_class: str,
        tenant_id: str = "system",
        agent_id: str = "anonymous",
        complexity: float | None = None,
        tokens: int | None = None,
    ) -> TierChoice:
        try:
            choice = self.chooser.choose(
                task_class=task_class,
                tenant_id=tenant_id,
                agent_id=agent_id,
                complexity=complexity,
                tokens=tokens,
            )
        except contract.ProxyError:
            raise
        except Exception as exc:  # noqa: BLE001 - finops BudgetBlocked/Validation
            name = type(exc).__name__
            if name == "BudgetBlocked":
                raise contract.BudgetBlockedError(str(exc)) from exc
            if name == "NoHealthyModelError":
                raise contract.NoHealthyRouteError(str(exc)) from exc
            raise contract.RoutingConfigError(f"chooser error {name}: {exc}") from exc
        return TierChoice(
            task_class=task_class,
            tier=choice.tier,
            estimated_cost_usd=choice.estimated_cost_usd,
            budget_action=choice.budget_action,
            provider=None,  # the proxy routing policy owns the provider chain
            model_label=choice.model.id,
            reasons=tuple(choice.reasons),
        )


class ProviderRegistryBackend:
    """Model-backend seam over the issue-#15 provider registry (offline)."""

    def __init__(self, registry) -> None:
        self.registry = registry

    def execute(
        self, candidate: RouteCandidate, invocation
    ) -> BackendResult:
        from providers.contract import ChatMessage
        from providers.errors import (
            CircuitOpenError,
            OutputValidationError,
            ProviderConfigurationError,
            ProviderError,
            ProviderTimeoutError,
            ProviderUnavailableError,
            RetryExhaustedError,
            SchemaDefinitionError,
        )

        registry = self.registry
        # Pin this candidate's provider as the tenant route for the registry
        # tier, then perform one resilient, stamped, audited provider call.
        registry.set_tenant_mapping(
            invocation.tenant_id, {candidate.registry_tier: candidate.provider}
        )
        messages = [
            ChatMessage(role=message.role, content=message.content)
            for message in invocation.messages
        ]
        try:
            result = registry.chat(
                messages=messages,
                schema=invocation.schema,
                tenant_id=invocation.tenant_id,
                agent_id=invocation.agent_id,
                logical_key=candidate.registry_tier,
            )
        except OutputValidationError as exc:
            raise BackendOutputInvalidError(str(exc)) from exc
        except (
            ProviderUnavailableError,
            ProviderTimeoutError,
            RetryExhaustedError,
            CircuitOpenError,
        ) as exc:
            raise BackendUnavailableError(str(exc)) from exc
        except (ProviderConfigurationError, SchemaDefinitionError, ProviderError) as exc:
            raise BackendConfigurationError(str(exc)) from exc
        return BackendResult(
            provider=result.provider,
            model=result.model,
            content=result.content,
            raw_text=result.raw_text,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=result.latency_ms,
        )


# --------------------------------------------------------------------------- #
# Offline provider transport rig (scripted canned responses, no sockets)
# --------------------------------------------------------------------------- #
def provider_response(
    provider: str,
    model: str,
    content: str,
    *,
    input_tokens: int = 120,
    output_tokens: int = 60,
) -> str:
    """Build a provider-appropriate HTTP body for a canned typed output."""
    import json

    if provider in ("deepseek", "openai"):
        body = {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
            "model": model,
        }
    elif provider == "anthropic":
        body = {
            "content": [{"type": "text", "text": content}],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            "model": model,
        }
    elif provider == "ollama":
        body = {
            "message": {"role": "assistant", "content": content},
            "prompt_eval_count": input_tokens,
            "eval_count": output_tokens,
            "model": model,
        }
    else:  # gemini shape
        body = {
            "candidates": [
                {
                    "content": {"parts": [{"text": content}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": input_tokens,
                "candidatesTokenCount": output_tokens,
            },
            "modelVersion": model,
        }
    return json.dumps(body)


class ProviderRig:
    """Scriptable offline transport rig for every provider of the registry."""

    def __init__(self) -> None:
        self._content: dict[str, str] = {}
        self._tokens: dict[str, tuple[int, int]] = {}
        self._fail: dict[str, Exception] = {}
        self._default_content: str = ""

    def transport_for(self, provider: str) -> "_RigTransport":
        return _RigTransport(self, provider)

    def set_default(self, content: str) -> None:
        self._default_content = content

    def script_success(
        self,
        provider: str,
        content: str,
        *,
        input_tokens: int = 120,
        output_tokens: int = 60,
    ) -> None:
        self._content[provider] = content
        self._tokens[provider] = (input_tokens, output_tokens)
        self._fail.pop(provider, None)

    def fail(self, provider: str, error: Exception) -> None:
        self._fail[provider] = error

    def healthy(self, provider: str) -> bool:
        return provider not in self._fail

    def request(
        self, provider: str, body: Mapping[str, Any] | None
    ) -> "object":
        from providers.transport import HttpResponse

        if provider in self._fail:
            raise self._fail[provider]
        content = self._content.get(provider)
        if content is None:
            content = self._default_content
        in_tokens, out_tokens = self._tokens.get(provider, (120, 60))
        model = (body or {}).get("model") or provider
        return HttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=provider_response(
                provider, str(model), content,
                input_tokens=in_tokens, output_tokens=out_tokens,
            ),
        )


class _RigTransport:
    """Per-provider transport delegating to the rig at request time."""

    def __init__(self, rig: ProviderRig, provider: str) -> None:
        self.rig = rig
        self.provider = provider

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
    ):
        return self.rig.request(self.provider, body)


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #
@dataclass
class WiredProxy:
    """The composed real-module gateway plus handles for tests/demos."""

    gateway: ModelGateway
    agent_resolver: PersonaAgentResolver
    task_resolver: PromptTaskResolver
    chooser_adapter: FinOpsChooserAdapter
    backend: ProviderRegistryBackend
    provider_registry: Any
    rig: ProviderRig
    limits_engine: Any
    audit_sink: CallRecordSink
    metering_sink: CallRecordSink
    finops_chooser: Any = None
    finops_sink: Any = None
    personas: Any = None
    prompts: Any = None
    mapping: Any = None


def build_real_gateway(
    *,
    health: HealthSignal = None,
    transport_credentials: str = "ao-test-key-not-a-secret",
    limits_engine: Any | None = None,
    finops_health: HealthSignal = None,
    audit_sink: CallRecordSink | None = None,
    metering_sink: CallRecordSink | None = None,
) -> WiredProxy:
    """Compose a ``ModelGateway`` over the real merged sibling modules.

    All provider traffic is offline: the provider registry is built with the
    scriptable ``ProviderRig`` transport and a fake credentials factory.  The
    FinOps chooser loads the real ``tiers.yaml``; the prompt and persona
    registries read the real published seeds.  ``limits_engine`` defaults to a
    plain ``LimitsEngine`` (observe budgets, cache disabled).
    """
    ensure_paths()

    from providers.registry import ProviderRegistry

    personas = load_personas()
    mapping = load_persona_mapping()
    prompts = load_prompts()
    prompts_registry = prompts.PromptRegistry()

    agent_resolver = PersonaAgentResolver(personas.PersonaRegistry(), mapping)
    task_resolver = PromptTaskResolver(prompts_registry)

    # --- FinOps chooser (issue #17), real tier table ---------------------- #
    import chooser as finops_chooser_mod
    import complexity as complexity_mod
    import loader as loader_mod
    import metering as finops_metering_mod

    table = loader_mod.load_tier_table()
    scorer = complexity_mod.DifficultyScorer()
    finops_sink = finops_metering_mod.ListMeteringSink()
    finops_chooser = finops_chooser_mod.ModelChooser(
        table=table,
        scorer=scorer,
        budget_enforcer=None,
        sink=finops_sink,
        health=finops_health,
    )
    chooser_adapter = FinOpsChooserAdapter(finops_chooser)

    # --- Provider registry (issue #15), offline transport rig ------------- #
    from providers.base import Credentials

    rig = ProviderRig()
    registry = ProviderRegistry(
        transport_factory=lambda provider, cfg: rig.transport_for(provider),
        credentials_factory=lambda tenant, provider: Credentials(
            api_key=transport_credentials
        ),
    )
    backend = ProviderRegistryBackend(registry)

    # --- Limits facade (issue #19) ---------------------------------------- #
    if limits_engine is None:
        from limits.limiter import LimitsEngine

        limits_engine = LimitsEngine()

    audit = audit_sink or ListCallRecordSink()
    metering = metering_sink or ListCallRecordSink()

    gateway = ModelGateway(
        agent_resolver=agent_resolver,
        task_resolver=task_resolver,
        chooser=chooser_adapter,
        backend=backend,
        limits=limits_engine,
        health=health,
        audit_sink=audit,
        metering_sink=metering,
    )
    return WiredProxy(
        gateway=gateway,
        agent_resolver=agent_resolver,
        task_resolver=task_resolver,
        chooser_adapter=chooser_adapter,
        backend=backend,
        provider_registry=registry,
        rig=rig,
        limits_engine=limits_engine,
        audit_sink=audit,
        metering_sink=metering,
        finops_chooser=finops_chooser,
        finops_sink=finops_sink,
        personas=personas,
        prompts=prompts,
        mapping=mapping,
    )
