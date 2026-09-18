"""The #506 class: a turn is judged in ITS OWN bucket, not the live clock's.

#506's two quota-refusal tests were green on 2026-09-14 — the day the lane ran —
and red every day after.  The fixture seeded the rail for ``DAY =
"2026-09-14"`` while the guard resolved the *evaluation* bucket from the live
clock, so the seed silently missed, an exhausted tenant was **allowed** through,
and the acceptance proof expired with the calendar.  A green that depends on
when it runs is not a control.

This module is the inversion of that trap.  Every instant below is a **literal**
past day, two of them, and the rail is exhausted in exactly one: the turn dated
that day must be refused, the turn dated the other must be allowed, and the
verdict must *report* the bucket it decided in.  Nothing here reads the clock,
so neither half can expire — and the pre-fix guard (whose bucket was
``today_utc()``) fails the refusal half on every calendar day, while the bucket
it reports could not be named at all.

Deliberately NOT a re-test of the rails themselves (``test_budget_guard`` and
``test_negative_controls`` do that): the only question here is *which window* a
turn is measured in.
"""

from __future__ import annotations

import pytest

from telemetry.budgets.budget import BudgetEnforcer, load_budget_policies
from telemetry.budgets.ledger import StaticLedger
from telemetry.budgets.model import (
    RESOURCE_REQUESTS,
    WINDOW_DAY,
    this_month_utc,
    today_utc,
)
from telemetry.budgets.quota import (
    QuotaEnforcer,
    QuotaLimit,
    QuotaPolicy,
    StaticProbe,
)
from telemetry.chat.budget_guard import GuardedTurnRunner, TurnBudgetGuard
from telemetry.chat.model import LEDGER_ACTION_REFUSED, ChatTurn
from telemetry.metering.model import NON_BILLABLE_OUTCOMES

#: The day #506's fixture pinned, and the day its rail is exhausted in.  A past
#: literal on purpose: it can never be "reached" by waiting.
EXHAUSTED_TS = "2026-09-14T09:00:00Z"
EXHAUSTED_DAY = "2026-09-14"
EXHAUSTED_MONTH = "2026-09"

#: A second past instant, in a different day AND month, where nothing is spent.
FRESH_TS = "2026-08-01T09:00:00Z"
FRESH_DAY = "2026-08-01"
FRESH_MONTH = "2026-08"

#: The daily request rail's shape (soft 10, hard 20) and acme's shipped cap.
HARD_LIMIT = 20
SHIPPED_MONTHLY_CAP_USD = 120.0


def turn_dated(world, ts: str, turn_id: str) -> ChatTurn:
    """A deterministic cold turn dated at ``ts``; everything else is the world."""
    return ChatTurn(
        turn_id=turn_id,
        conversation_id=world.conversation,
        tenant_id=world.tenant,
        agent_id=world.agent,
        ts=ts,
        ticket_id=world.ticket,
        static_prefix=world.static_prefix,
        user_delta=world.user_delta,
        cached_tokens=0,
    )


def quota_rail_exhausted_on(world, day: str) -> QuotaEnforcer:
    """The quota rail with its daily call ledger seeded at the hard limit."""
    policy = QuotaPolicy(
        tenant_id=world.tenant,
        plan="free",
        limits={
            RESOURCE_REQUESTS: QuotaLimit(
                resource=RESOURCE_REQUESTS,
                window=WINDOW_DAY,
                soft_limit=10,
                hard_limit=HARD_LIMIT,
            )
        },
    )
    ledger = StaticLedger(calls={(world.tenant, day): HARD_LIMIT})
    return QuotaEnforcer(ledger, {world.tenant: policy}, probe=StaticProbe())


def quota_runner_exhausted_on(world, attributor, day: str) -> GuardedTurnRunner:
    """A runner whose quota rail — and nothing else — is exhausted on ``day``."""
    guard = TurnBudgetGuard(quota=quota_rail_exhausted_on(world, day))
    return GuardedTurnRunner(guard, attributor)


def month_rail_spent_in(world, month: str, spend_usd: float) -> BudgetEnforcer:
    """acme's shipped policies over a monthly spend seeded in ``month``."""
    ledger = StaticLedger(costs={(world.tenant, month): spend_usd})
    return BudgetEnforcer(ledger, load_budget_policies())


def test_a_turn_is_judged_in_its_own_day_not_the_live_clock(
    world, attributor, spy_provider
):
    """One rail, two dated turns: only the turn in the seeded day is refused.

    The rail is exhausted on ``EXHAUSTED_DAY`` and nothing is spent on
    ``FRESH_DAY``, and both days are past literals in the same rail's window
    shape.  The *pair* is the proof: a guard that refused everything fails the
    second half, and a guard that resolved the bucket from the live clock (the
    #506 defect) fails the first half on every calendar day — while the bucket
    each verdict reports is the assertion a pre-fix guard cannot satisfy at all.
    """
    exhausted_provider = spy_provider()
    fresh_provider = spy_provider()

    refused = quota_runner_exhausted_on(world, attributor, EXHAUSTED_DAY).run(
        turn_dated(world, EXHAUSTED_TS, "turn-exhausted"), exhausted_provider
    )
    allowed = quota_runner_exhausted_on(world, attributor, EXHAUSTED_DAY).run(
        turn_dated(world, FRESH_TS, "turn-fresh"), fresh_provider
    )

    assert refused.allowed is False, "the turn is dated in the exhausted day"
    assert refused.provider_called is False
    assert exhausted_provider.call_count == 0

    assert allowed.allowed is True, "the same rail is fresh in this turn's day"
    assert allowed.provider_called is True
    assert fresh_provider.call_count == 1

    assert (refused.outcome.day, refused.outcome.month) == (
        EXHAUSTED_DAY,
        EXHAUSTED_MONTH,
    ), "the verdict must name the bucket it judged in"
    assert (allowed.outcome.day, allowed.outcome.month) == (FRESH_DAY, FRESH_MONTH)


def test_a_refused_past_dated_turn_is_still_metered(
    world, attributor, usage_store, ledger, spy_provider
):
    """The #506 acceptance criterion, dated by the turn and not by the run.

    A turn dated 2026-09-14, against the quota rail exhausted on 2026-09-14, is
    refused *and still metered* (AO-GR-18): one non-billable metering row and
    exactly one ledger event, whatever today is.
    """
    provider = spy_provider()
    runner = quota_runner_exhausted_on(world, attributor, EXHAUSTED_DAY)

    result = runner.run(
        turn_dated(world, EXHAUSTED_TS, "turn-metered"),
        provider,
        estimated_cost_usd=0.01,
    )

    assert result.allowed is False
    assert result.outcome.hard_stop is True
    assert result.outcome.outcome in NON_BILLABLE_OUTCOMES
    assert (result.outcome.day, result.outcome.month) == (
        EXHAUSTED_DAY,
        EXHAUSTED_MONTH,
    )
    assert provider.call_count == 0

    assert usage_store.count() == 1, "a refused turn is still metered"
    row = usage_store.read()[0]
    assert row.metered is True
    assert row.billable is False
    assert row.cost_usd is None

    records = ledger.records(world.tenant)
    assert len(records) == 1, "exactly one ledger event"
    assert records[0]["action"] == LEDGER_ACTION_REFUSED


def test_a_monthly_rail_is_judged_in_the_turns_own_month(world, attributor, spy_provider):
    """The month bucket carried the same bomb, with a longer fuse.

    A month rail resolves to ``this_month_utc()`` when nothing pins it, so a
    monthly fixture is correct only while the live month *is* the month it
    seeds: the sibling over-cap test passes today (2026-09) and would expire on
    2026-10-01 exactly as #506's day-pinned tests expired on 2026-09-15.  Both
    halves below are literals, so neither can expire.
    """
    exhausted_provider = spy_provider()
    fresh_provider = spy_provider()

    refused = GuardedTurnRunner(
        TurnBudgetGuard(
            budget=month_rail_spent_in(world, EXHAUSTED_MONTH, SHIPPED_MONTHLY_CAP_USD)
        ),
        attributor,
    ).run(turn_dated(world, EXHAUSTED_TS, "turn-over-cap"), exhausted_provider)
    allowed = GuardedTurnRunner(
        TurnBudgetGuard(
            budget=month_rail_spent_in(world, EXHAUSTED_MONTH, SHIPPED_MONTHLY_CAP_USD)
        ),
        attributor,
    ).run(turn_dated(world, FRESH_TS, "turn-other-month"), fresh_provider)

    assert refused.allowed is False, "spend at the cap in the turn's own month refuses"
    assert refused.outcome.month == EXHAUSTED_MONTH
    assert exhausted_provider.call_count == 0

    assert allowed.allowed is True, "the same spend in another month must not refuse"
    assert allowed.outcome.month == FRESH_MONTH
    assert fresh_provider.call_count == 1


def test_an_explicit_bucket_still_wins_over_the_turn_timestamp(
    world, attributor, spy_provider
):
    """The derivation is a default, not a cage: a caller can still pin.

    ``GuardedTurnRunner.run`` threads an explicit ``day``/``month`` through to
    the guard, so a deliberate pin (an audit replay, a re-scored window) is
    honoured even when it is *not* the turn's own bucket.
    """
    provider = spy_provider()
    runner = quota_runner_exhausted_on(world, attributor, EXHAUSTED_DAY)

    result = runner.run(
        turn_dated(world, FRESH_TS, "turn-pinned"),
        provider,
        estimated_cost_usd=0.01,
        day=EXHAUSTED_DAY,
        month=EXHAUSTED_MONTH,
    )

    assert result.allowed is False, "the pinned day is the one the rail is exhausted in"
    assert (result.outcome.day, result.outcome.month) == (
        EXHAUSTED_DAY,
        EXHAUSTED_MONTH,
    )
    assert provider.call_count == 0


def test_a_turn_with_no_timestamp_is_judged_in_the_live_bucket(
    world, attributor, spy_provider
):
    """A live turn is unchanged: an absent ``ts`` normalizes to now.

    ``ChatTurn.normalized_ts`` fills an absent timestamp with the current
    instant, so a live turn's derived bucket *is* today's — the pre-fix
    behaviour for a live turn, preserved exactly.  Both the seed and the
    expectation are read from that same live bucket, so this test cannot rot the
    way a seed pinned to a *fixed* day did.
    """
    live_day = today_utc()
    live_month = this_month_utc()
    live_turn = ChatTurn(
        turn_id="turn-live",
        conversation_id=world.conversation,
        tenant_id=world.tenant,
        agent_id=world.agent,
        static_prefix=world.static_prefix,
        user_delta=world.user_delta,
        cached_tokens=0,
    )
    provider = spy_provider()
    runner = quota_runner_exhausted_on(world, attributor, live_day)

    result = runner.run(live_turn, provider, estimated_cost_usd=0.01)

    assert (result.outcome.day, result.outcome.month) == (live_day, live_month)
    assert result.allowed is False, "a live turn meets the live day's exhausted rail"
    assert provider.call_count == 0


@pytest.mark.parametrize(
    ("ts", "expected_day", "expected_month"),
    [
        (EXHAUSTED_TS, EXHAUSTED_DAY, EXHAUSTED_MONTH),
        (FRESH_TS, FRESH_DAY, FRESH_MONTH),
        ("2025-01-02T23:59:59Z", "2025-01-02", "2025-01"),
        ("2025-12-31T00:00:01Z", "2025-12-31", "2025-12"),
    ],
)
def test_the_bucket_is_plainly_the_turns_own_timestamp(
    world, ts, expected_day, expected_month
):
    """The derivation is the consumed ``ts[:10]`` / ``ts[:7]`` bucket helpers.

    A guard with no rails wired cannot refuse, so this control is only about
    *which* window the verdict reports — the cheapest possible statement of the
    scope rule, including both sides of a month and a year boundary.
    """
    outcome = TurnBudgetGuard().check(turn_dated(world, ts, "turn-bucket"))

    assert outcome.day == expected_day
    assert outcome.month == expected_month
    assert outcome.allowed is True
