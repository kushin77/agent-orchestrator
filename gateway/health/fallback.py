"""Local-model fallback chain for high availability (issue #18).

The HA doctrine for commercial providers: when a model is unhealthy the
gateway routes away from it along an ordered **fallback chain** that ends at
the local Ollama provider — the last-resort rung that keeps the agent
producing when every commercial provider is down (the defragsuite / gov-ai-
scout cloud -> local pattern that gateway/providers issue #15 also mirrors).

Ollama is represented as a **provider like any other** in the health layer:
its outcomes are recorded on the monitor and it can be marked healthy or
unhealthy (e.g. an out-of-band check finds the local daemon down). Because it
is the final rung of every chain, the resolver only lands on it when it is
healthy — and when even the local model is down the chain reports **no
healthy route** (``resolve`` returns ``None``) so the caller can escalate
rather than silently send traffic to a dead model.

Chain shape::

    primary (deepseek/deepseek-chat)
      -> alternate commercial rung (anthropic/claude-haiku-4-5)
      -> LOCAL LAST RESORT (ollama/llama3.2)

``ChainRegistry.resolve(provider, model, health_lookup)`` walks the rungs in
order and returns the first one that is healthy, skipping degraded /
quarantined rungs. A missing explicit chain degrades to ``[primary, local
last resort]`` — every provider falls back to Ollama.

When the local provider rungs (hermes / paperclip / ollama) are unreachable
there is no rung left inside the chain. Issue #375 adds one **post-chain
remote rung**: the shared platform model server (``kushin77/shared-services``,
``services/model-server/``). It is declared in ``health.yaml`` under
``chains.sharedServices``, tried by ``resolve`` only *after* the ordered rungs
are exhausted and only when it is healthy — a single bounded attempt, never a
loop — and it lives outside the chains so the ``rungs[-1].local`` invariant is
untouched. Its endpoint is read from a declared environment variable at call
time. With no rung declared, resolution is byte-for-byte the previous
behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Mapping

try:  # PyYAML is a declared repo dependency; keep the import local.
    import yaml
except ImportError:  # pragma: no cover - exercised only on broken installs
    yaml = None  # type: ignore[assignment]

#: Health lookup signature: (provider, model) -> healthy?
HealthLookup = Callable[[str, str], bool]

#: Canonical provider key for the local fallback (consumed from providers #15).
LOCAL_PROVIDER = "ollama"

#: Default local model id (matches providers' Ollama LOW/MED default).
DEFAULT_LOCAL_MODEL = "llama3.2"

#: The shipped local last resort (see health.yaml ``chains.localLastResort``).
DEFAULT_LOCAL_RUNG = "ollama/llama3.2"

#: Canonical provider key for the remote shared-platform rung (issue #375).
#: It names the shared model server shipped by the ``kushin77/shared-services``
#: platform (``services/model-server/``, ``services/mcp-hub/``,
#: ``agents/ollama/``) — a rung that lives *outside* this repo's own fleet.
SHARED_SERVICES_PROVIDER = "shared-services"

#: Default model id requested from the shared model server.
DEFAULT_SHARED_SERVICES_MODEL = "llama3.2"

#: Name of the environment variable that carries the shared-services endpoint.
#: The endpoint host is NEVER hardcoded in the resolution path: it is read from
#: this declared variable at call time (declared in ``health.yaml`` under
#: ``chains.sharedServices.endpointEnv``).
DEFAULT_SHARED_SERVICES_ENDPOINT_ENV = "AO_SHARED_SERVICES_MODEL_URL"

#: Documented non-secret endpoint used only when the declared env var is unset.
#: It matches the current local convention (the Ollama default port); the
#: *resolution path* stays env-driven even when this default applies.
DEFAULT_SHARED_SERVICES_ENDPOINT = "http://localhost:11434"


def split_route(route: str) -> tuple[str, str]:
    """Split a ``provider/model`` route string into its two parts."""
    if "/" not in route:
        raise ValueError(f"route {route!r} must be 'provider/model'")
    provider, model = route.split("/", 1)
    if not provider or not model:
        raise ValueError(f"route {route!r} has an empty provider or model")
    return provider, model


@dataclass(frozen=True)
class FallbackRung:
    """One step of a fallback chain (a concrete provider+model)."""

    provider: str
    model: str
    local: bool = False

    @property
    def route(self) -> str:
        return f"{self.provider}/{self.model}"

    def to_dict(self) -> dict[str, object]:
        return {"provider": self.provider, "model": self.model, "local": self.local}


@dataclass(frozen=True)
class SharedServicesRung(FallbackRung):
    """The remote shared-services rung (issue #375) — a post-chain fallback.

    The local last resort is the final rung *of a chain*; this rung lives
    **outside** every chain, so a chain's ``rungs[-1].local`` invariant is
    untouched. ``ChainRegistry.resolve`` tries it only after the ordered rungs
    are exhausted and only when it is healthy — a single bounded attempt, never
    a loop. When the local provider rungs (hermes / paperclip / ollama) are
    unreachable the gateway therefore *degrades* onto the shared platform
    instead of failing hard (the fleet crash-loop trigger, #366).

    The endpoint is resolved from the declared environment variable at call
    time (``endpoint_env``); ``default_endpoint`` is a documented non-secret
    fallback used only when that variable is unset.
    """

    endpoint_env: str = DEFAULT_SHARED_SERVICES_ENDPOINT_ENV
    default_endpoint: str = DEFAULT_SHARED_SERVICES_ENDPOINT

    @property
    def endpoint(self) -> str:
        """The endpoint URL, read from the declared env var at call time."""
        return os.environ.get(self.endpoint_env) or self.default_endpoint

    def to_dict(self) -> dict[str, object]:
        data = super().to_dict()
        data["endpointEnv"] = self.endpoint_env
        data["endpoint"] = self.endpoint
        return data


@dataclass(frozen=True)
class FallbackChain:
    """An ordered chain for one primary route; the last rung is local Ollama.

    ``rungs[0]`` is the primary target; the final rung is the local
    last-resort model. Rungs are consumed in order, skipping unhealthy ones.
    """

    key: str  # the primary ``provider/model`` this chain serves
    rungs: tuple[FallbackRung, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.rungs:
            raise ValueError(f"chain {self.key!r} has no rungs")
        if self.rungs[0].route != self.key:
            raise ValueError(
                f"chain {self.key!r} must start with its primary "
                f"({self.rungs[0].route!r})"
            )
        if not self.rungs[-1].local:
            raise ValueError(
                f"chain {self.key!r} must end at the local last resort "
                f"(got {self.rungs[-1].route!r})"
            )

    @property
    def primary(self) -> FallbackRung:
        return self.rungs[0]

    @property
    def last_resort(self) -> FallbackRung:
        """The local Ollama rung that closes the chain."""
        return self.rungs[-1]

    def to_dict(self) -> dict[str, object]:
        return {"key": self.key, "rungs": [r.to_dict() for r in self.rungs]}


def _parse_rung(item: str | Mapping[str, object]) -> FallbackRung:
    if isinstance(item, str):
        provider, model = split_route(item)
        return FallbackRung(provider=provider, model=model, local=provider == LOCAL_PROVIDER)
    if isinstance(item, Mapping):
        provider = str(item.get("provider") or "")
        model = str(item.get("model") or "")
        if not provider or not model:
            raise ValueError(f"rung entry missing provider/model: {item!r}")
        local = bool(item.get("local", provider == LOCAL_PROVIDER))
        return FallbackRung(provider=provider, model=model, local=local)
    raise ValueError(f"unparseable rung entry: {item!r}")


def _parse_shared_services(item: object) -> SharedServicesRung | None:
    """Parse the optional ``chains.sharedServices`` rung (issue #375).

    Absent/``None`` means the deployment declares no shared-services rung; the
    registry then behaves byte-for-byte as before this change.
    """
    if item is None:
        return None
    if not isinstance(item, Mapping):
        raise ValueError(f"sharedServices must be a mapping, got {item!r}")
    provider = str(item.get("provider") or SHARED_SERVICES_PROVIDER)
    model = str(item.get("model") or DEFAULT_SHARED_SERVICES_MODEL)
    if not provider or not model:
        raise ValueError(f"sharedServices needs provider + model: {item!r}")
    endpoint_env = str(item.get("endpointEnv") or DEFAULT_SHARED_SERVICES_ENDPOINT_ENV)
    default_endpoint = str(item.get("endpoint") or DEFAULT_SHARED_SERVICES_ENDPOINT)
    return SharedServicesRung(
        provider=provider,
        model=model,
        endpoint_env=endpoint_env,
        default_endpoint=default_endpoint,
    )


class ChainRegistry:
    """Explicit fallback chains + the default cloud -> local route.

    Optionally carries one ``shared_services`` rung: the remote shared-platform
    model server tried by :meth:`resolve` *after* the ordered rungs are
    exhausted and before refusing a route (issue #375).
    """

    def __init__(
        self,
        chains: Mapping[str, FallbackChain] | None = None,
        local_last_resort: str = DEFAULT_LOCAL_RUNG,
        shared_services: SharedServicesRung | None = None,
    ) -> None:
        self._chains: dict[str, FallbackChain] = dict(chains or {})
        self._local = split_route(local_last_resort)
        self._shared_services = shared_services

    # -- construction ------------------------------------------------------- #
    def add_chain(self, chain: FallbackChain) -> None:
        self._chains[chain.key] = chain

    def chain_for(self, provider: str, model: str) -> FallbackChain:
        """The chain for a primary route (explicit, else cloud -> local)."""
        route = f"{provider}/{model}"
        chain = self._chains.get(route)
        if chain is not None:
            return chain
        local_rung = FallbackRung(
            provider=self._local[0], model=self._local[1], local=True
        )
        return FallbackChain(key=route, rungs=(FallbackRung(provider, model), local_rung))

    def chains(self) -> dict[str, FallbackChain]:
        return dict(self._chains)

    @property
    def shared_services(self) -> SharedServicesRung | None:
        """The declared post-chain shared-services rung, or ``None``.

        ``None`` means no rung is declared, in which case :meth:`resolve` is
        byte-for-byte the pre-#375 behaviour (backward compatible).
        """
        return self._shared_services

    # -- resolution --------------------------------------------------------- #
    def resolve(
        self, provider: str, model: str, health: HealthLookup
    ) -> FallbackRung | None:
        """The first healthy rung of the chain for (provider, model).

        Skips degraded / quarantined rungs (per the injected health lookup). If
        no ordered rung is healthy, the declared post-chain shared-services rung
        is tried **once** (issue #375) — it must itself be healthy. When even
        that is unhealthy (or no rung is declared) the method returns ``None``,
        so the caller escalates instead of sending traffic to a dead model
        (no-false-green: never fall through to a dead rung, never a silent
        success). Bounded by construction: one ordered walk + at most one
        shared-services attempt, so a dead rung can never cause unbounded
        re-dispatch.
        """
        chain = self.chain_for(provider, model)
        for rung in chain.rungs:
            if health(rung.provider, rung.model):
                return rung
        shared = self._shared_services
        if shared is not None and health(shared.provider, shared.model):
            return shared
        return None

    # -- YAML --------------------------------------------------------------- #
    @classmethod
    def from_yaml(cls, path: str) -> "ChainRegistry":
        """Load chains from the ``chains:`` section of a health YAML document.

        Shape (see ``health.yaml``)::

            chains:
              localLastResort: ollama/llama3.2
              sharedServices:
                provider: shared-services
                model: llama3.2
                endpointEnv: AO_SHARED_SERVICES_MODEL_URL
              explicit:
                - key: deepseek/deepseek-chat
                  rungs:
                    - { provider: deepseek, model: deepseek-chat }
                    - { provider: anthropic, model: claude-haiku-4-5 }
                    - { provider: ollama, model: llama3.2, local: true }

        ``rungs`` may also be plain ``provider/model`` strings. The optional
        ``sharedServices`` mapping is the post-chain remote rung (issue #375),
        held separately from the chains because it is tried after — never
        inside — a chain's ordered rungs.
        """
        if yaml is None:  # pragma: no cover - broken install only
            raise RuntimeError("PyYAML is required to load fallback chains")
        with open(path, "r", encoding="utf-8") as fh:
            document = yaml.safe_load(fh) or {}
        chains_section = document.get("chains") or {}
        if not isinstance(chains_section, dict):
            raise ValueError(f"{path}: 'chains' must be a mapping")
        local = str(chains_section.get("localLastResort") or DEFAULT_LOCAL_RUNG)
        shared = _parse_shared_services(chains_section.get("sharedServices"))
        registry = cls(local_last_resort=local, shared_services=shared)
        for entry in chains_section.get("explicit") or []:
            key = str(entry.get("key") or "")
            raw_rungs = entry.get("rungs") or []
            if not key or not raw_rungs:
                raise ValueError(f"{path}: explicit chain needs key + rungs: {entry!r}")
            rungs = tuple(_parse_rung(r) for r in raw_rungs)
            registry.add_chain(FallbackChain(key=key, rungs=rungs))
        return registry


def _default_chains_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "health.yaml")


def load_default_chains() -> ChainRegistry:
    """Load the shipped fallback chains from ``health.yaml``."""
    return ChainRegistry.from_yaml(_default_chains_path())
