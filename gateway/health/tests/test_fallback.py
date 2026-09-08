"""Local-model fallback chain tests (issue #18 AC 3, deliverable 3).

Ollama is the local last resort: every chain ends at a local Ollama rung, the
resolver only lands on it when it is healthy, and when even Ollama is down
the chain reports **no healthy route** (None) — never a silent fall-through
to a dead model (no-false-green).
"""

from __future__ import annotations

import pytest

from health import (
    DEFAULT_LOCAL_RUNG,
    LOCAL_PROVIDER,
    ChainRegistry,
    FallbackChain,
    FallbackRung,
    HealthMonitor,
    load_default_chains,
)


def test_shipped_chains_all_end_at_local_ollama() -> None:
    registry = load_default_chains()
    assert registry.chains()
    for key, chain in registry.chains().items():
        assert chain.key == key
        assert chain.primary.route == key
        assert chain.last_resort.local is True
        assert chain.last_resort.provider == LOCAL_PROVIDER
        assert chain.last_resort.route == DEFAULT_LOCAL_RUNG


def test_shipped_monitor_config_loads_with_chains() -> None:
    from health import load_config

    config = load_config()
    assert config.window_size == 100
    assert config.min_samples == 10
    assert config.degrade_failure_pct == 20.0
    assert config.trip_failure_pct == 50.0


def test_all_healthy_routes_to_primary() -> None:
    m = HealthMonitor()
    registry = load_default_chains()
    rung = registry.resolve("deepseek", "deepseek-chat", m.is_healthy)
    assert rung == FallbackRung("deepseek", "deepseek-chat", local=False)


def test_unhealthy_primary_falls_back_to_alternate(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    # Establish a healthy baseline, then degrade the deepseek primary
    # (5 successes + 2 failures = 28.6% failure -> degraded band).
    for _ in range(5):
        m.record_success("deepseek", "deepseek-chat", latency_ms=300.0)
    for _ in range(2):
        m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    assert m.health_status("deepseek", "deepseek-chat")["verdict"] == "degraded"
    registry = load_default_chains()
    rung = registry.resolve("deepseek", "deepseek-chat", m.is_healthy)
    assert rung is not None
    assert rung.provider == "anthropic"  # the shipped alternate
    assert rung.local is False


def test_ollama_marked_unhealthy_means_no_route(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    registry = load_default_chains()
    # Take down every rung of the deepseek chain: primary, alternate, ollama.
    m.mark_unhealthy("deepseek", "deepseek-chat", detail="cloud incident")
    m.mark_unhealthy("anthropic", "claude-haiku-4-5", detail="alternate down")
    m.mark_unhealthy("ollama", "llama3.2", detail="ollama daemon down")
    # Even though ollama is the last resort, a dead last resort is a dead end.
    assert registry.resolve("deepseek", "deepseek-chat", m.is_healthy) is None
    assert m.is_healthy("ollama", "llama3.2") is False


def test_ollama_restored_is_the_last_resort(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    registry = load_default_chains()
    m.mark_unhealthy("deepseek", "deepseek-chat", detail="cloud incident")
    m.mark_unhealthy("anthropic", "claude-haiku-4-5", detail="alternate down")
    # Ollama still healthy -> the resolver lands on the local last resort.
    rung = registry.resolve("deepseek", "deepseek-chat", m.is_healthy)
    assert rung is not None
    assert rung.local is True
    assert rung.route == DEFAULT_LOCAL_RUNG


def test_default_chain_for_unlisted_route_ends_at_ollama() -> None:
    registry = load_default_chains()
    chain = registry.chain_for("openai", "gpt-4o")
    assert [r.route for r in chain.rungs] == ["openai/gpt-4o", DEFAULT_LOCAL_RUNG]
    assert chain.last_resort.local is True


def test_default_chain_falls_back_to_ollama_when_primary_down(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    m.mark_unhealthy("openai", "gpt-4o", detail="down")
    registry = load_default_chains()
    rung = registry.resolve("openai", "gpt-4o", m.is_healthy)
    assert rung is not None
    assert rung.route == DEFAULT_LOCAL_RUNG


def test_chain_must_start_with_its_primary() -> None:
    with pytest.raises(ValueError):
        FallbackChain(
            key="deepseek/deepseek-chat",
            rungs=(
                FallbackRung("anthropic", "claude-haiku-4-5"),
                FallbackRung("ollama", "llama3.2", local=True),
            ),
        )


def test_chain_must_end_at_local_last_resort() -> None:
    with pytest.raises(ValueError):
        FallbackChain(
            key="deepseek/deepseek-chat",
            rungs=(FallbackRung("deepseek", "deepseek-chat"),),
        )


def test_empty_chain_rejected() -> None:
    with pytest.raises(ValueError):
        FallbackChain(key="deepseek/deepseek-chat", rungs=())


def test_route_parsing_roundtrip() -> None:
    registry = ChainRegistry()
    rung = FallbackRung("ollama", "llama3.2", local=True)
    registry.add_chain(FallbackChain(key="x/y", rungs=(FallbackRung("x", "y"), rung)))
    assert registry.chain_for("x", "y").last_resort.route == "ollama/llama3.2"
