"""Health-aware fallback tests (issue #17 AC 5).

Health is an injected signal (dict or predicate of model id -> healthy) so the
chooser can prefer a fallback model when the primary is unhealthy without any
live provider. Asserts:

- an unhealthy primary falls back to the next candidate WITHIN the same tier
- an entirely unhealthy tier escalates to the next tier (respecting maxTier)
- a capped class with no healthy model raises NoHealthyModelError
- the same behavior works with a callable health signal
"""

from __future__ import annotations

import pytest

from chooser import ModelChooser, NoHealthyModelError


@pytest.fixture()
def table_l0_ids(table):
    return {m.id for m in table.tier("L0").models}


def test_unhealthy_primary_falls_back_within_tier(table, table_l0_ids) -> None:
    primary = table.tier("L0").cheapest.id  # cheapest L0 model (deepseek-v4-flash)
    health = {primary: False}
    chooser = ModelChooser(table=table, health=health)
    choice = chooser.choose(task_class="code-author", complexity=5.0)
    assert choice.tier == "L0"  # stayed in the tier
    assert choice.model.id != primary  # not the unhealthy primary
    assert choice.model.id in table_l0_ids  # still an L0-capable model


def test_entire_tier_unhealthy_escalates(table) -> None:
    l0_ids = {m.id for m in table.tier("L0").models}
    health = {mid: False for mid in l0_ids}  # whole L0 tier down
    chooser = ModelChooser(table=table, health=health)
    # research may escalate (maxTier L2): expect L1, not a crash.
    choice = chooser.choose(task_class="research", complexity=5.0)
    assert choice.tier == "L1"
    assert any("no-healthy-model@L0" in r for r in choice.reasons)


def test_capped_class_with_no_healthy_model_raises(table) -> None:
    l0_ids = {m.id for m in table.tier("L0").models}
    health = {mid: False for mid in l0_ids}
    chooser = ModelChooser(table=table, health=health)
    # finops-meter is capped at L0 and L0 is entirely down: no fallback exists.
    with pytest.raises(NoHealthyModelError):
        chooser.choose(task_class="finops-meter", complexity=5.0)


def test_health_signal_accepts_callable(table) -> None:
    primary = table.tier("L0").cheapest.id
    chooser = ModelChooser(
        table=table,
        health=lambda mid: mid != primary,  # only the primary is down
    )
    choice = chooser.choose(task_class="code-author", complexity=5.0)
    assert choice.tier == "L0"
    assert choice.model.id != primary


def test_healthy_primary_is_still_cheapest(table) -> None:
    chooser = ModelChooser(table=table, health={})  # empty map = everything healthy
    choice = chooser.choose(task_class="code-author", complexity=5.0)
    assert choice.tier == "L0"
    assert choice.model.id == table.tier("L0").cheapest.id
