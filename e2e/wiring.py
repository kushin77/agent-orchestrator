"""e2e/wiring — composition root over the REAL merged pillar modules (offline).

Issue #46's E2E gate wires the merged product modules into one offline
control plane: tenant signup (identity/onboarding), authorization
(identity/rbac), agent identity (registry/service + events), guarded routed
model calls (gateway/proxy + providers + finops + health + limits), inline
guardrails (dlp/policy/honesty), audit (telemetry/ledger), usage billing
(telemetry/metering) and durable task execution (engine/core).

Nothing here edits a pillar file: the merged modules are consumed read-only
through their own public APIs, exactly as their suites do.  Every provider
call is a canned offline response (gateway/proxy/wiring's scriptable rig) —
no sockets, no network, no real keys (synthetic materials only).

Two known integration facts are handled locally (documented in README):

1. ``gateway/proxy/wiring.build_real_gateway`` builds its audit/metering sinks
   with ``audit_sink or ListCallRecordSink()``; ``ListCallRecordSink`` defines
   ``__len__`` so an EMPTY injected sink is falsy and gets silently replaced.
   :class:`TruthyListCallRecordSink` (``__bool__`` always True) keeps the
   injected sink in place so tests can observe the records.
2. ``telemetry/`` is imported through the repo-root PEP-420 namespace, never
   as a top-level root, so gateway/finops' plain top-level ``metering``
   module keeps resolving to itself.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from e2e._paths import ensure_sys_paths  # noqa: F401  (idempotent)
from e2e._paths import REPO_ROOT as _REPO_ROOT

# The tier ladder is READ from its declared authority (#1494) — the AgentProfile
# catalog `registry/profiles/catalog.yaml` ``tiers``, through its one reader —
# rather than restated here as a comprehension over a literal tuple.
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from registry.profiles.tiers import authority as _tier_authority  # noqa: E402

ensure_sys_paths()

# --------------------------------------------------------------------------- #
# Synthetic (non-secret) offline materials.  Assembled at runtime so the
# committed files never carry a literal secret shape (GR-6).
# --------------------------------------------------------------------------- #
LEDGER_KEY_BYTES = bytes(range(32))           # deterministic 32-byte test key
RUNTIME_SIGNING_KEY = b"ao46-e2e-runtime-signing-key-material"
DLP_HMAC_KEY = b"ao46-e2e-dlp-hmac-key-" + b"material"

# DLP egress allowlist (provider endpoints the golden path may dispatch to).
ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "ollama": "http://localhost:11434/api/chat",
    "openai": "https://api.openai.com/v1/chat/completions",
    "copilot": "https://api.githubcopilot.com/chat/completions",
    "paperclip": "http://localhost:11435/api/chat",
    "hermes": "http://localhost:11436/api/chat",
}

# Provider names as the providers-registry / routing policy know them.
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_OLLAMA = "ollama"  # the keyless local terminal hop of every chain
PROVIDER_PAPERCLIP = "paperclip"  # purebliss team member (local, no live endpoint)
PROVIDER_HERMES = "hermes"  # purebliss team member (local; falls back to ollama)
PROVIDER_COPILOT = "copilot"  # purebliss team member (OpenAI-compatible adapter)

TENANT = "acme"

# The six-agent purebliss team (epic #253 + issue #340): agent id -> provider.
TEAM_AGENT_PROVIDERS = {
    "ollama": PROVIDER_OLLAMA,
    "paperclip": PROVIDER_PAPERCLIP,
    "hermes": PROVIDER_HERMES,
    "deepseek": PROVIDER_DEEPSEEK,
    "claude": PROVIDER_ANTHROPIC,
    "copilot": PROVIDER_COPILOT,
}
# Provider ids exercised by the six-agent team (claude routes to anthropic).
TEAM_PROVIDERS = (
    PROVIDER_OLLAMA,
    PROVIDER_PAPERCLIP,
    PROVIDER_HERMES,
    PROVIDER_DEEPSEEK,
    PROVIDER_ANTHROPIC,
    PROVIDER_COPILOT,
)

# --------------------------------------------------------------------------- #
# Truthy sink (integration fact 1 above).
# --------------------------------------------------------------------------- #
class TruthyListCallRecordSink:
    """In-memory gateway call-record sink that is always truthy.

    Mirrors the real ``proxy/sinks.ListCallRecordSink`` record contract but
    does not define ``__len__``/``__bool__`` from its contents, so an empty
    sink is not treated as falsy by ``build_real_gateway``'s
    ``audit_sink or ListCallRecordSink()`` expression.
    """

    def __init__(self) -> None:
        self.records: List[Any] = []

    def record(self, record: Any) -> None:
        self.records.append(record)

    @property
    def count(self) -> int:
        return len(self.records)

    def latest_dict(self) -> Dict[str, Any]:
        return dict(self.records[-1].to_dict())


# --------------------------------------------------------------------------- #
# Control plane (composition root)
# --------------------------------------------------------------------------- #
class ControlPlane:
    """A fully provisioned, offline control plane over the real modules."""

    def __init__(self, tenant_id: str = TENANT, work_dir: Optional[str] = None) -> None:
        ensure_sys_paths()
        self.tenant_id = tenant_id
        self.work_dir = work_dir
        self.attestations: List[Any] = []
        self._build()

    # -- low-level helpers ------------------------------------------------- #
    def attest(
        self,
        guard_id: str,
        exit_code: int,
        evidence: str,
        *,
        provenance: str = "",
        controls: Optional[List[str]] = None,
    ) -> Any:
        """Record a GuardAttestation (honesty model) for one verdict."""
        from honesty import GuardAttestation

        attestation = GuardAttestation.record(
            guard_id=guard_id,
            exit_code=exit_code,
            evidence=evidence,
            provenance=provenance,
            controls=controls,
        )
        self.attestations.append(attestation)
        return attestation

    def _build(self) -> None:
        """Provision the tenant and wire every real module instance."""
        from identity.onboarding.provisioning import ProvisionSpec, provision
        from identity.onboarding.store import InMemoryStore as OnboardingStore
        from rbac.store import InMemoryStore as RbacStore

        from service import AgentRegistry
        from proxy.sinks import ListCallRecordSink  # noqa: F401 (protocol doc)
        from proxy.wiring import build_real_gateway

        # --- 1. tenant signup (identity/onboarding) ---------------------- #
        self.onboarding_store = OnboardingStore()
        self.rbac_store = RbacStore()
        spec = ProvisionSpec(
            slug=self.tenant_id,
            name="Acme Corp",
            tenant_type="platform",
            idp_tenant_id="idp-acme",
            domain="acme.example.com",
            owner_email="admin@acme.example.com",
            owner_name="Acme Admin",
            owner_role="owner",
        )
        self.provision_result = provision(self.onboarding_store, self.rbac_store, spec)
        self.tenant = self.provision_result.tenant
        self.owner_subject = spec.owner_email

        # --- 2. agent registry (registry/service + events) --------------- #
        self.registry = AgentRegistry()
        self.registry.register(
            self.tenant_id, "worker-1", "coder", actor=f"user:{self.owner_subject}"
        )
        self.registry.register(
            self.tenant_id, "reviewer-1", "reviewer", actor=f"user:{self.owner_subject}"
        )
        self.registry.activate(self.tenant_id, "worker-1", actor="user:admin")
        self.registry.activate(self.tenant_id, "reviewer-1", actor="user:admin")
        self.worker_session = self.registry.issue_session(
            self.tenant_id, "worker-1", signing_key=RUNTIME_SIGNING_KEY
        )

        # --- 3. model gateway (gateway/proxy + providers/finops/limits) -- #
        self.audit_sink = TruthyListCallRecordSink()
        self.metering_sink = TruthyListCallRecordSink()
        self.wired = build_real_gateway(
            audit_sink=self.audit_sink, metering_sink=self.metering_sink
        )

        # --- 4. inline guardrails (dlp) ---------------------------------- #
        self._build_dlp()

        # --- 5. inline guardrails (policy) ------------------------------- #
        self._build_policy()

        # --- 6. audit + usage billing (telemetry) ------------------------ #
        self._build_telemetry()

        # --- 7. durable execution (engine/core) -------------------------- #
        self._build_engine()

    # -- dlp --------------------------------------------------------------- #
    def _build_dlp(self) -> None:
        from urllib.parse import urlsplit

        from dlp.egress import AllowedTarget, EgressGuard
        from dlp.hmac_audit import HmacAuditLog, HmacSigner
        from dlp.pipeline import EgressPipeline
        from dlp.telemetry import SecurityTelemetry

        # Allow each provider's canonical host (empty path prefix = any path).
        allowlist = {
            self.tenant_id: [
                AllowedTarget(provider, str(urlsplit(ENDPOINTS[provider]).hostname))
                for provider in ENDPOINTS
            ]
        }
        signer = HmacSigner(key=DLP_HMAC_KEY)
        self.dlp_pipeline = EgressPipeline(
            guard=EgressGuard(allowlist),
            signer=signer,
            audit_log=HmacAuditLog(signer),
            telemetry=SecurityTelemetry(),
        )
        self.dlp_allowlist = allowlist

    # -- policy ------------------------------------------------------------ #
    def _build_policy(self) -> None:
        from policy import ControlRegistry, PolicyBundle, PolicyEngine
        from policy.loader import policy_from_mapping

        self.policy_engine = PolicyEngine(
            PolicyBundle(
                (
                    policy_from_mapping(self._model_call_policy_doc(), source="e2e:golden-path"),
                )
            ),
            controls=ControlRegistry(),
        )

    @staticmethod
    def _model_call_policy_doc() -> Dict[str, Any]:
        """Deny-list policy over ``model.call`` (allow by default, block on flag)."""
        return {
            "id": "e2e-model-call-guard",
            "default": "log",
            "rules": [
                {
                    "id": "block-flagged-call",
                    "actions": ["model.call"],
                    "decision": "block",
                    "reason": "model call blocked for tenant {tenant} by e2e policy",
                    "condition": {"path": "security.flag", "op": "eq", "value": True},
                }
            ],
        }

    # -- telemetry --------------------------------------------------------- #
    def _build_telemetry(self) -> None:
        from telemetry.ledger.keystore import DictKeystore, KeyMaterial
        from telemetry.ledger.store import open_ledger
        from telemetry.metering.intake import MeteringIntake
        from telemetry.metering.ratecards import RateCardStore
        from telemetry.metering.store import MemoryUsageStore
        from telemetry.metering.report import UsageReporter

        self.ledger_dir = (
            os.path.join(self.work_dir, "ledger")
            if self.work_dir
            else None
        )
        keystore = DictKeystore(
            {self.tenant_id: KeyMaterial(key=LEDGER_KEY_BYTES, key_id="ao46:k1")}
        )
        self.ledger_keystore = keystore
        self.ledger_store = open_ledger(self.ledger_dir, keystore=keystore)
        self.metering_store = MemoryUsageStore()
        self.metering_intake = MeteringIntake(
            rate_store=RateCardStore.load_dir(), store=self.metering_store
        )
        self.usage_reporter = UsageReporter(self.metering_store)

    # -- engine ------------------------------------------------------------ #
    def _build_engine(self) -> None:
        from engine.core import (
            Engine,
            FileJsonlEventStore,
            NamespaceRegistry,
            WorkflowSpec,
        )
        from engine.core import Step, StepKind, WorkflowKind

        event_path = (
            os.path.join(self.work_dir, "engine-events.jsonl") if self.work_dir else None
        )
        self.engine_store = FileJsonlEventStore(path=event_path) if event_path else None
        self.engine_namespaces = NamespaceRegistry()
        if self.engine_namespaces.get(self.tenant_id) is None:
            self.engine_namespaces.create(self.tenant_id)
        self.engine = Engine(
            store=self.engine_store,
            namespaces=self.engine_namespaces,
            gateway=_RealGatewayAdapter(self.wired),
        )
        self._engine_spec = WorkflowSpec(
            name="e2e-durable-task",
            kind=WorkflowKind.TASK_EXECUTION,
            steps=[
                Step(
                    step_id="classify",
                    kind=StepKind.TASK,
                    name="classify-route",
                    handler="core.task",
                    args={
                        "agent_id": "orchestrator",
                        "task_type": "classify-route",
                    },
                )
            ],
        )

    # -- audit convenience ------------------------------------------------- #
    def ledger_append_model_call(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Record one model.call in the tamper-evident ledger."""
        return self.ledger_store.append(
            self.tenant_id,
            actor="agent:" + record.get("agentId", "worker-1"),
            action="model.call",
            resource="gateway/proxy",
            model_used=record.get("model"),
            cost_usd=str(record.get("estimatedCostUsd", "") or ""),
            payload={"provider": record.get("provider"), "outcome": record.get("outcome")},
        )

    def ledger_append_policy_decision(self, decision: str) -> Dict[str, Any]:
        return self.ledger_store.append(
            self.tenant_id,
            actor="system:policy",
            action="policy.decision",
            resource="guardrails/policy",
            payload={"decision": str(decision)},
        )


class _RealGatewayAdapter:
    """Adapts the REAL gateway/proxy ModelGateway to the engine/core gateway
    port (``engine.core.gateway_port.ModelGateway`` protocol)."""

    def __init__(self, wired: Any) -> None:
        self._wired = wired

    def dispatch(self, request: Any) -> Any:
        from proxy.model import TaskRequest
        from engine.core import GatewayResult

        proxy_result = self._wired.gateway.dispatch(
            request.agent_id,
            TaskRequest(
                tenant_id=request.tenant_id,
                task_type=request.task_type,
                input=dict(request.input_),
            ),
        )
        record = proxy_result.record
        return GatewayResult(
            outcome=proxy_result.outcome,
            content=proxy_result.content,
            provider=proxy_result.provider or "",
            model=proxy_result.model or "",
            cost=float(record.estimated_cost_usd) if record is not None else 0.0,
            usage={
                "input_tokens": record.input_tokens if record is not None else 0,
                "output_tokens": record.output_tokens if record is not None else 0,
            },
        )


def build_control_plane(
    tenant_id: str = TENANT, work_dir: Optional[str] = None
) -> ControlPlane:
    """Build a fresh offline control plane (one per test / run)."""
    if work_dir is not None:
        os.makedirs(work_dir, exist_ok=True)
    return ControlPlane(tenant_id=tenant_id, work_dir=work_dir)


def _json_default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "value"):
        return o.value
    if hasattr(o, "name"):
        return o.name
    return str(o)


def serialize_attestations(attestations: List[Any]) -> List[Dict[str, Any]]:
    return [a.to_dict() for a in attestations]


def write_evidence(work_dir: str, name: str, payload: Dict[str, Any]) -> str:
    """Persist one evidence document (JSON) under the run directory."""
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=_json_default)
        fh.write("\n")
    return path


# --------------------------------------------------------------------------- #
# purebliss five-agent team: the REAL paperclip/hermes adapters own their names
# --------------------------------------------------------------------------- #
def assert_team_providers_registered() -> None:
    """Require the REAL paperclip + hermes adapters to be registered (issue #255).

    This replaced ``install_team_provider_stubs`` (issue #257): the offline stub
    adapters it installed were a documented FALLBACK whose guard was a no-op,
    because the real ``gateway/providers/{paperclip,hermes}.py`` adapters always
    own those provider names. Installing a stub could therefore only ever mask a
    missing real adapter; asserting presence keeps the e2e funnel honest about
    which adapter it exercises.
    """
    from providers.hermes import HermesProvider
    from providers.paperclip import PaperclipProvider
    from providers.registry import PROVIDER_CLASSES

    for name, expected in (
        ("paperclip", PaperclipProvider),
        ("hermes", HermesProvider),
    ):
        actual = PROVIDER_CLASSES.get(name)
        if actual is not expected:
            raise AssertionError(
                f"the real {name} adapter is not registered: expected "
                f"{expected.__module__}.{expected.__qualname__}, got {actual!r}"
            )


def _team_provider_configs() -> List[Any]:
    from providers.config import ProviderConfig

    def _local(name: str, port: int, model: str) -> ProviderConfig:
        return ProviderConfig(
            name=name,
            base_url=f"http://localhost:{port}",
            api_path="/api/chat",
            tier_models={tier: model for tier in _tier_authority()},
            default_model=model,
            supported_models=frozenset({model}),
            requires_key=False,
            fallback=("ollama",),
        )

    return [_local("paperclip", 11435, "paperclip-1"), _local("hermes", 11436, "hermes-1")]


def register_team_provider_configs(registry: Any) -> None:
    """Add the local team provider configs to a built provider registry."""
    for config in _team_provider_configs():
        registry.register_provider_config(config)


def extend_team_routing(wired: Any) -> None:
    """Append the local team providers as terminal fallback hops on every
    routing chain so the six-agent team is routable offline (issue #257,
    issue #340).

    Runtime-only: the merged routing policy (``gateway/proxy/config/routing.yaml``)
    predates the purebliss team and this lane never edits a gateway file.
    """
    config = wired.gateway.router.config
    for tier, chain in list(config.provider_chains.items()):
        if "paperclip" not in chain:
            config.provider_chains[tier] = tuple(chain) + ("paperclip", "hermes")
        if "copilot" not in config.provider_chains[tier]:
            config.provider_chains[tier] = tuple(config.provider_chains[tier]) + (
                "copilot",
            )


def build_team_gateway(
    *,
    health: Any = None,
    audit_sink: Any = None,
    metering_sink: Any = None,
) -> Any:
    """``build_real_gateway`` plus the purebliss team's offline local providers.

    Requires the real paperclip + hermes adapters to be registered, adds their
    keyless local configs, and extends the routing chains so every team provider
    is routable (issue #257).
    """
    from proxy.wiring import build_real_gateway

    assert_team_providers_registered()
    wired = build_real_gateway(
        health=health, audit_sink=audit_sink, metering_sink=metering_sink
    )
    register_team_provider_configs(wired.provider_registry)
    extend_team_routing(wired)
    return wired
