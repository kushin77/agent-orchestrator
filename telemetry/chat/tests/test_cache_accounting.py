"""Prompt-cache accounting: a cache-hit turn is reported, and costs less.

The comparison is on a deterministic fixture: the same prompt, the same model,
the shipped rate card — the only difference is the observed prefix reuse.
"""

from __future__ import annotations

import pytest

from telemetry.chat.cache_accounting import (
    KIND_COLD,
    KIND_PARTIAL,
    CacheAccounting,
    CacheAccountingError,
    account_prefix,
)
from telemetry.chat.model import ChatTurn


def test_a_cache_warm_turn_costs_less_than_the_cold_turn(
    attributor, turn, cold_turn, record_factory, world
):
    warm = attributor.attribute(turn, record_factory(turn_id=turn.turn_id))
    cold = attributor.attribute(
        cold_turn,
        record_factory(
            turn_id=cold_turn.turn_id, input_tokens=world.prompt_tokens
        ),
    )

    assert warm.cache.cached_tokens > 0
    assert cold.cache.cached_tokens == 0
    assert cold.cache.kind == KIND_COLD
    assert warm.cache.kind == KIND_PARTIAL
    assert warm.cost_usd < cold.cost_usd, "prefix reuse must show up in the price"
    assert warm.cold_equivalent_cost_usd == pytest.approx(cold.cost_usd, abs=1e-12)
    assert warm.cache_hit_share > 0.0
    assert cold.cache_hit_share == 0.0


def test_the_reported_cache_share_is_the_prefix_reuse(world, turn):
    cache = turn.cache_accounting(output_tokens=world.output_tokens)

    assert cache.prompt_tokens == world.static_tokens + world.delta_tokens
    assert cache.cacheable is True
    # the reported share is rounded to 6 dp (a figure a surface can render)
    assert cache.hit_share == pytest.approx(
        round(world.cached_tokens / world.prompt_tokens, 6), abs=1e-12
    )
    assert cache.hit_share_of_prefix == pytest.approx(
        round(world.cached_tokens / world.static_tokens, 6), abs=1e-12
    )
    assert cache.billable_input_tokens == world.billable_input_tokens
    assert cache.to_dict()["cacheHitShare"] == cache.hit_share


def test_an_impossible_cache_report_is_refused(world):
    prefix = account_prefix(world.static_prefix, world.user_delta)
    with pytest.raises(CacheAccountingError) as excinfo:
        CacheAccounting(prefix=prefix, cached_tokens=prefix.static_tokens + 1)

    assert "impossible cache report" in str(excinfo.value)


def test_a_dynamic_prefix_is_accounted_uncacheable(world):
    prefix = account_prefix(world.dynamic_prefix, world.user_delta)

    assert prefix.cacheable is False
    assert prefix.max_cacheable_tokens == 0
    assert prefix.dynamic_tokens, "the offending tokens must be named"
    assert prefix.reason and "dynamic tokens" in prefix.reason


def test_an_uncacheable_prefix_cannot_report_a_cache_hit(world):
    dynamic_turn = ChatTurn(
        turn_id="turn-dynamic",
        conversation_id=world.conversation,
        tenant_id=world.tenant,
        agent_id=world.agent,
        ts=world.ts,
        static_prefix=world.dynamic_prefix,
        user_delta=world.user_delta,
        cached_tokens=1,
    )
    with pytest.raises(CacheAccountingError):
        dynamic_turn.cache_accounting(output_tokens=0)


def test_a_turn_without_a_cache_observation_is_accounted_cold(world):
    unobserved = ChatTurn(
        turn_id="turn-unobserved",
        conversation_id=world.conversation,
        tenant_id=world.tenant,
        agent_id=world.agent,
        ts=world.ts,
        static_prefix=world.static_prefix,
        user_delta=world.user_delta,
    )
    cache = unobserved.cache_accounting(output_tokens=10)

    assert cache.cached_tokens == 0
    assert cache.kind == KIND_COLD
    assert cache.billable_input_tokens == cache.prompt_tokens


def test_the_prefix_fingerprint_is_content_addressed(world):
    first = account_prefix(world.static_prefix, world.user_delta)
    same = account_prefix(world.static_prefix, "a different question entirely")
    other = account_prefix(world.dynamic_prefix, world.user_delta)

    assert first.fingerprint == same.fingerprint, "the prefix is what is cached"
    assert first.fingerprint != other.fingerprint


def test_negative_cached_tokens_are_refused(world):
    prefix = account_prefix(world.static_prefix, world.user_delta)
    with pytest.raises(CacheAccountingError):
        CacheAccounting(prefix=prefix, cached_tokens=-1)
