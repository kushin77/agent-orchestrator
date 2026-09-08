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


class ChainRegistry:
    """Explicit fallback chains + the default cloud -> local route."""

    def __init__(
        self,
        chains: Mapping[str, FallbackChain] | None = None,
        local_last_resort: str = DEFAULT_LOCAL_RUNG,
    ) -> None:
        self._chains: dict[str, FallbackChain] = dict(chains or {})
        self._local = split_route(local_last_resort)

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

    # -- resolution --------------------------------------------------------- #
    def resolve(
        self, provider: str, model: str, health: HealthLookup
    ) -> FallbackRung | None:
        """The first healthy rung of the chain for (provider, model).

        Skips degraded / quarantined rungs (per the injected health lookup);
        returns ``None`` when every rung — including the local Ollama last
        resort — is unhealthy, so the caller escalates instead of sending
        traffic to a dead model (no-false-green: never fall through to a dead
        final rung).
        """
        chain = self.chain_for(provider, model)
        for rung in chain.rungs:
            if health(rung.provider, rung.model):
                return rung
        return None

    # -- YAML --------------------------------------------------------------- #
    @classmethod
    def from_yaml(cls, path: str) -> "ChainRegistry":
        """Load chains from the ``chains:`` section of a health YAML document.

        Shape (see ``health.yaml``)::

            chains:
              localLastResort: ollama/llama3.2
              explicit:
                - key: deepseek/deepseek-chat
                  rungs:
                    - { provider: deepseek, model: deepseek-chat }
                    - { provider: anthropic, model: claude-haiku-4-5 }
                    - { provider: ollama, model: llama3.2, local: true }

        ``rungs`` may also be plain ``provider/model`` strings.
        """
        if yaml is None:  # pragma: no cover - broken install only
            raise RuntimeError("PyYAML is required to load fallback chains")
        with open(path, "r", encoding="utf-8") as fh:
            document = yaml.safe_load(fh) or {}
        chains_section = document.get("chains") or {}
        if not isinstance(chains_section, dict):
            raise ValueError(f"{path}: 'chains' must be a mapping")
        local = str(chains_section.get("localLastResort") or DEFAULT_LOCAL_RUNG)
        registry = cls(local_last_resort=local)
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
