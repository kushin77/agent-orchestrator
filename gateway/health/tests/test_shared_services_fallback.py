"""Shared-services fallback rung tests (issue #375).

Before this change ``ChainRegistry.resolve`` returned ``None`` whenever every
ordered rung (including the local Ollama last resort) was unhealthy, and the
caller failed hard — the fleet crash-loop trigger (#366). Issue #375 adds one
**post-chain remote rung** onto the shared platform model server
(``kushin77/shared-services``, ``services/model-server/``), tried once after
the ordered rungs are exhausted and only when it is healthy.

These tests pin the four behaviours the contract requires:

1. local Ollama unhealthy + shared rung healthy -> resolve lands on the shared
   rung (degrade, do not fail hard);
2. all rungs (including the shared rung) unhealthy -> ``None`` (explicit
   refusal, never a silent success);
3. no ``sharedServices`` declared -> byte-for-byte the previous behaviour
   (backward compatible);
4. the shared rung endpoint resolves from the *declared env var* at call time
   (env wins; the documented non-secret default applies only when unset).

They also pin boundedness: exactly one shared-services attempt, never a loop.
"""

from __future__ import annotations

from health import HealthMonitor
from health.fallback import (
    DEFAULT_SHARED_SERVICES_ENDPOINT,
    DEFAULT_SHARED_SERVICES_ENDPOINT_ENV,
    SHARED_SERVICES_PROVIDER,
    ChainRegistry,
    FallbackRung,
    SharedServicesRung,
    load_default_chains,
)

#: The deepseek chain's ordered rungs shipped in health.yaml.
_DEEPSEEK_RUNGS = (
    ("deepseek", "deepseek-chat"),
    ("anthropic", "claude-haiku-4-5"),
    ("ollama", "llama3.2"),
)


def _take_down_local_rungs(m: HealthMonitor) -> None:
    """Mark every ordered rung of the shipped deepseek chain unhealthy."""
    for provider, model in _DEEPSEEK_RUNGS:
        m.mark_unhealthy(provider, model, detail="local/cloud incident")


def _shipped_shared_rung() -> SharedServicesRung:
    rung = load_default_chains().shared_services
    assert rung is not None, "health.yaml must declare chains.sharedServices"
    return rung


def test_shipped_health_yaml_declares_the_shared_rung() -> None:
    registry = load_default_chains()
    rung = registry.shared_services
    assert isinstance(rung, SharedServicesRung)
    assert rung.provider == SHARED_SERVICES_PROVIDER
    assert rung.model == "llama3.2"
    assert rung.endpoint_env == DEFAULT_SHARED_SERVICES_ENDPOINT_ENV
    # The rung is remote, not the local last resort, and sits outside chains.
    assert rung.local is False
    assert rung.route == "shared-services/llama3.2"
    for chain in registry.chains().values():
        assert chain.last_resort.local is True
        assert SHARED_SERVICES_PROVIDER not in {r.provider for r in chain.rungs}


def test_local_rungs_down_lands_on_shared_services(config, clock) -> None:
    """Local Ollama unreachable + shared rung healthy -> shared-services rung."""
    m = HealthMonitor(config=config, now=clock)
    _take_down_local_rungs(m)
    # The local last resort really is unreachable (the pre-#375 dead end).
    assert m.is_healthy("ollama", "llama3.2") is False
    registry = load_default_chains()
    rung = registry.resolve("deepseek", "deepseek-chat", m.is_healthy)
    assert isinstance(rung, SharedServicesRung)
    assert rung.provider == SHARED_SERVICES_PROVIDER
    assert rung.route == "shared-services/llama3.2"


def test_all_rungs_down_including_shared_refuses_explicitly(config, clock) -> None:
    """Every rung unhealthy, shared rung included -> ``None`` (no silent success)."""
    m = HealthMonitor(config=config, now=clock)
    _take_down_local_rungs(m)
    m.mark_unhealthy(SHARED_SERVICES_PROVIDER, "llama3.2", detail="shared down")
    registry = load_default_chains()
    assert registry.shared_services is not None
    assert m.is_healthy(SHARED_SERVICES_PROVIDER, "llama3.2") is False
    assert registry.resolve("deepseek", "deepseek-chat", m.is_healthy) is None


def test_no_shared_rung_declared_is_backward_compatible(tmp_path) -> None:
    """Without ``chains.sharedServices`` the pre-#375 behaviour is unchanged."""
    doc = tmp_path / "health.yaml"
    doc.write_text(
        "schemaVersion: 1\n"
        "chains:\n"
        "  localLastResort: ollama/llama3.2\n"
        "  explicit:\n"
        "    - key: deepseek/deepseek-chat\n"
        "      rungs:\n"
        "        - { provider: deepseek, model: deepseek-chat }\n"
        "        - { provider: ollama, model: llama3.2, local: true }\n",
        encoding="utf-8",
    )
    registry = ChainRegistry.from_yaml(str(doc))
    assert registry.shared_services is None

    # A fully-dead chain refuses a route exactly as it did before the change.
    def nothing_healthy(provider: str, model: str) -> bool:
        return False

    assert registry.resolve("deepseek", "deepseek-chat", nothing_healthy) is None
    # A healthy ordered rung still wins, unaffected by the new code path.
    def all_healthy(provider: str, model: str) -> bool:
        return True

    primary = registry.resolve("deepseek", "deepseek-chat", all_healthy)
    assert primary == FallbackRung("deepseek", "deepseek-chat", local=False)


def test_default_registry_has_no_shared_rung() -> None:
    """A programmatic registry without the argument declares no shared rung."""
    assert ChainRegistry().shared_services is None
    assert ChainRegistry(shared_services=None).shared_services is None


def test_shared_rung_endpoint_comes_from_the_declared_env(monkeypatch) -> None:
    """The endpoint resolves from the declared env var at call time."""
    rung = _shipped_shared_rung()
    env_name = rung.endpoint_env
    assert env_name, "the rung must declare an endpoint env var name"

    monkeypatch.setenv(env_name, "http://shared-services.internal:8080/v1")
    assert rung.endpoint == "http://shared-services.internal:8080/v1"

    # Unset -> the documented non-secret default, resolution path still env-driven.
    monkeypatch.delenv(env_name, raising=False)
    assert rung.endpoint == DEFAULT_SHARED_SERVICES_ENDPOINT

    # Empty string is treated as unset, never as a usable endpoint.
    monkeypatch.setenv(env_name, "")
    assert rung.endpoint == DEFAULT_SHARED_SERVICES_ENDPOINT


def test_resolve_makes_exactly_one_shared_services_attempt(config, clock) -> None:
    """Bounded: one ordered walk + one shared attempt, never a loop."""
    m = HealthMonitor(config=config, now=clock)
    _take_down_local_rungs(m)
    m.mark_unhealthy(SHARED_SERVICES_PROVIDER, "llama3.2", detail="shared down")
    registry = load_default_chains()
    chain = registry.chain_for("deepseek", "deepseek-chat")
    calls: list[tuple[str, str]] = []

    def counted(provider: str, model: str) -> bool:
        calls.append((provider, model))
        return m.is_healthy(provider, model)

    assert registry.resolve("deepseek", "deepseek-chat", counted) is None
    ordered = [(r.provider, r.model) for r in chain.rungs]
    assert calls == ordered + [(SHARED_SERVICES_PROVIDER, "llama3.2")]


def test_shared_rung_is_not_a_chain_rung() -> None:
    """The chain invariant (final rung local) is untouched by the shared rung."""
    for chain in load_default_chains().chains().values():
        assert chain.rungs[-1].local is True
