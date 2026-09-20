#!/usr/bin/env python3
"""Provider credit budget tests: the declaration, the arithmetic, the refusals.

Two things are under test, and the second matters more:

1. the shipped declaration (`provider-credits.yaml`) matches the provider's own
   published contract - the plan ceilings, the top-up purchase amounts, and the
   fact that the contract declares **no balance endpoint** (so the meter must
   not read as a live readback); and
2. every rule is **provoked**: a half-declared price, a wrong schema version, an
   undeclared plan/provider/top-up and a non-ascending top-up list are each
   refused by name, and an unpublished price answers *unmetered* (`None`) rather
   than `0.0`.

Rule 2 is the load-bearing half. `None`-not-zero is the rate-card doctrine
(issue #33) applied to a prepaid budget: a call whose price is unknown must
surface as unmetered, never as free.
"""

from __future__ import annotations

import shutil

import pytest
import yaml

from provider_credits import (
    CREDITS_PATH,
    BILLING_CREDITS,
    TRACKING_MANUAL_TOP_UPS,
    CreditDeclarationError,
    UnknownPlanError,
    UnknownProviderError,
    UnknownTopUpError,
    load_credits,
)

SHIPPED = load_credits()


# --------------------------------------------------------------------------- #
# 1. The shipped declaration matches the published contract
# --------------------------------------------------------------------------- #


def test_only_nous_is_declared_and_it_is_the_shipped_file() -> None:
    assert SHIPPED.providers() == ("nous",)
    assert SHIPPED.path == str(CREDITS_PATH)


def test_nous_declares_the_published_api_contract() -> None:
    card = SHIPPED.card_or_raise("nous")
    assert card.display_name == "Nous Research"
    assert card.billing == BILLING_CREDITS
    assert card.base_url == "https://inference-api.nousresearch.com/v1"
    assert card.docs == "https://portal.nousresearch.com/api-docs"


def test_the_contract_declares_no_balance_endpoint() -> None:
    """The API documents chat/completions only - no credits readback."""
    card = SHIPPED.card_or_raise("nous")
    assert card.balance_endpoint is None
    assert card.balance_tracking == TRACKING_MANUAL_TOP_UPS
    assert card.balance_tracking_note, "the manual-tracking caveat must be stated"


def test_plan_ceilings_are_the_published_numbers() -> None:
    card = SHIPPED.card_or_raise("nous")
    assert card.credit_ceiling_usd("free") == 0.0
    assert card.credit_ceiling_usd("plus") == 22.0
    assert card.credit_ceiling_usd("super") == 110.0
    assert card.credit_ceiling_usd("ultra") == 220.0
    # the price paid for those credits, and the rollover cap, are distinct facts
    assert card.plan("plus").price_usd_per_month == 20.0
    assert card.plan("plus").rollover_cap_usd == 10.0
    assert card.plan("super").price_usd_per_month == 100.0
    assert card.plan("super").rollover_cap_usd == 50.0
    assert card.plan("ultra").price_usd_per_month == 200.0
    assert card.plan("ultra").rollover_cap_usd == 100.0


def test_top_ups_are_the_published_purchase_amounts() -> None:
    card = SHIPPED.card_or_raise("nous")
    assert card.top_ups_usd == (10.0, 20.0, 50.0, 100.0, 200.0)


def test_the_tier_map_models_are_declared_and_priced() -> None:
    """The four probe-verified ids the provider's tier map routes to."""
    card = SHIPPED.card_or_raise("nous")
    assert set(card.models) == {
        "inclusionai/ling-3.0-flash",
        "qwen/qwen3.7-flash",
        "anthropic/claude-haiku-4.5",
        "openai/gpt-6-astra-fast",
    }
    assert card.unmetered_models() == ()
    assert card.rate_for("inclusionai/ling-3.0-flash").context_window == 262144
    assert card.rate_for("openai/gpt-6-astra-fast").context_window == 1050000


def test_the_tier_ladder_ascends_by_published_rate() -> None:
    """LOW < MED < HIGH < MAX on the endpoint's own published prices."""
    card = SHIPPED.card_or_raise("nous")
    ladder = [
        "inclusionai/ling-3.0-flash",
        "qwen/qwen3.7-flash",
        "anthropic/claude-haiku-4.5",
        "openai/gpt-6-astra-fast",
    ]
    inputs = [card.rate_for(m).input_usd_per_mtok for m in ladder]
    assert inputs == sorted(inputs), f"the ladder is not ascending: {inputs}"
    assert len(set(inputs)) == len(inputs), "two rungs share a rate"


def test_a_model_the_chat_path_refuses_is_not_priced() -> None:
    """Priced ids must be the servable ones, not the wider catalog listing."""
    card = SHIPPED.card_or_raise("nous")
    # listed in GET /v1/models, but the chat path answers 400 "Unknown model"
    for unservable in ("deepseek/deepseek-v4-flash", "anthropic/claude-opus-5"):
        assert card.rate_for(unservable) is None


def test_the_dead_documented_hermes_ids_are_not_declared() -> None:
    """The docs advertise Hermes-4.x ids the live service cannot serve (#1559)."""
    card = SHIPPED.card_or_raise("nous")
    for dead in ("Hermes-4.3-36B", "Hermes-4-70B", "Hermes-4-405B", "hermes3"):
        assert card.rate_for(dead) is None


# --------------------------------------------------------------------------- #
# 2. unknown -> None, never 0 (the load-bearing rule)
# --------------------------------------------------------------------------- #


def test_an_unpublished_price_is_unmetered_and_never_zero() -> None:
    """unknown -> None, never 0: the rule the whole declaration exists to hold."""
    table = _load_mutated(
        "        pricePublished: true\n        inputUsdPerMTok: 0.03\n"
        "        outputUsdPerMTok: 0.13\n",
        "        pricePublished: false\n        inputUsdPerMTok: null\n"
        "        outputUsdPerMTok: null\n",
        count=1,
    )
    card = table.card_or_raise("nous")
    assert card.unmetered_models() == ("qwen/qwen3.7-flash",)
    debit = card.debit_usd("qwen/qwen3.7-flash", 10_000, 2_000)
    assert debit is None, "an unknown price must not be reported as a cost"
    assert debit != 0.0, "an unmetered call must never read as free"


def test_an_undeclared_model_is_unmetered_not_refused() -> None:
    """The call happened; this declaration simply cannot price it."""
    card = SHIPPED.card_or_raise("nous")
    assert card.rate_for("hermes3") is None
    assert card.debit_usd("hermes3", 100, 100) is None


def test_an_undeclared_provider_is_unmetered_from_the_table() -> None:
    assert SHIPPED.debit_usd("hermes", "hermes3", 100, 100) is None
    assert SHIPPED.card("hermes") is None


def test_a_published_price_meters_exactly() -> None:
    card = SHIPPED.card_or_raise("nous")
    # 10_000 in * 0.021/1M + 2_000 out * 0.063/1M = 0.00021 + 0.000126
    assert card.debit_usd("inclusionai/ling-3.0-flash", 10_000, 2_000) == pytest.approx(
        0.000336
    )
    # a full 1M tokens at one rung's rate is exactly its list price
    assert card.debit_usd("openai/gpt-6-astra-fast", 1_000_000, 0) == pytest.approx(20.0)
    assert card.debit_usd("anthropic/claude-haiku-4.5", 0, 1_000_000) == pytest.approx(
        5.0
    )


def test_negative_token_counts_are_refused_not_clamped() -> None:
    card = SHIPPED.card_or_raise("nous")
    with pytest.raises(ValueError):
        card.debit_usd("qwen/qwen3.7-flash", -1, 0)


# --------------------------------------------------------------------------- #
# 3. The budget line, and the refusals
# --------------------------------------------------------------------------- #


def test_budget_line_names_its_balance_source() -> None:
    card = SHIPPED.card_or_raise("nous")
    row = card.budget_line("plus", top_ups_usd=[50.0], debited_usd=3.5)
    assert row["provider"] == "nous"
    assert row["billing"] == "credits"
    assert row["ceilingUsd"] == 22.0
    assert row["topUpsUsd"] == [50.0]
    assert row["remainingUsd"] == pytest.approx(22.0 + 50.0 - 3.5)
    # the row must not read as a readback of the provider's balance
    assert row["balanceSource"] == TRACKING_MANUAL_TOP_UPS
    assert row["balanceEndpoint"] is None


def test_remaining_credits_adds_top_ups_to_the_ceiling() -> None:
    card = SHIPPED.card_or_raise("nous")
    assert card.remaining_credits_usd("super", [10.0, 200.0], 0.0) == 320.0
    assert card.remaining_credits_usd("super", [], 110.0) == 0.0


def test_an_undeclared_plan_is_refused_by_name() -> None:
    card = SHIPPED.card_or_raise("nous")
    with pytest.raises(UnknownPlanError) as refusal:
        card.credit_ceiling_usd("platinum")
    assert "platinum" in str(refusal.value)
    assert "plus" in str(refusal.value), "the refusal must name what IS declared"


def test_an_undeclared_provider_is_refused_by_name() -> None:
    with pytest.raises(UnknownProviderError) as refusal:
        SHIPPED.card_or_raise("anthropic")
    assert "anthropic" in str(refusal.value)
    assert "nous" in str(refusal.value)


def test_an_undeclared_top_up_amount_is_refused_by_name() -> None:
    card = SHIPPED.card_or_raise("nous")
    with pytest.raises(UnknownTopUpError) as refusal:
        card.remaining_credits_usd("plus", [37.0], 0.0)
    assert "37.0" in str(refusal.value)


# --------------------------------------------------------------------------- #
# 4. Provocations: the loader refuses a half-declared or malformed file
# --------------------------------------------------------------------------- #


def _load_mutated(old: str, new: str, *, count: int = 1):
    """Load the shipped declaration with one text mutation applied.

    Asserts the mutation actually landed, so a provocation that silently
    no-ops cannot pass as a refusal.
    """
    import tempfile
    from pathlib import Path

    scratch = Path(tempfile.mkdtemp(prefix="ao1559-credits."))
    try:
        target = scratch / "provider-credits.yaml"
        shutil.copy(CREDITS_PATH, target)
        original = target.read_text(encoding="utf-8")
        mutated = original.replace(old, new, count)
        assert mutated != original, "the mutation did not match the shipped file"
        target.write_text(mutated, encoding="utf-8")
        return load_credits(target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_a_published_price_with_a_null_rate_is_refused() -> None:
    """Half-declared is a declaration bug, not a zero."""
    with pytest.raises(CreditDeclarationError) as refusal:
        _load_mutated(
            "        pricePublished: true\n        inputUsdPerMTok: 0.021\n"
            "        outputUsdPerMTok: 0.063\n",
            "        pricePublished: true\n        inputUsdPerMTok: null\n"
            "        outputUsdPerMTok: 0.063\n",
            count=1,
        )
    assert "pricePublished is true but a rate is null" in str(refusal.value)


def test_an_unpublished_price_with_a_declared_rate_is_refused() -> None:
    with pytest.raises(CreditDeclarationError) as refusal:
        _load_mutated(
            "        pricePublished: true\n        inputUsdPerMTok: 0.021\n"
            "        outputUsdPerMTok: 0.063\n",
            "        pricePublished: false\n        inputUsdPerMTok: 0.021\n"
            "        outputUsdPerMTok: 0.063\n",
            count=1,
        )
    assert "pricePublished is false but a rate is declared" in str(refusal.value)


def test_a_wrong_schema_version_is_refused() -> None:
    with pytest.raises(CreditDeclarationError) as refusal:
        _load_mutated("schemaVersion: 1", "schemaVersion: 2")
    assert "schemaVersion" in str(refusal.value)


def test_a_non_ascending_top_up_list_is_refused() -> None:
    with pytest.raises(CreditDeclarationError) as refusal:
        _load_mutated(
            "topUpsUsd: [10.0, 20.0, 50.0, 100.0, 200.0]",
            "topUpsUsd: [10.0, 5.0, 50.0, 100.0, 200.0]",
        )
    assert "strictly ascending" in str(refusal.value)


def test_a_negative_plan_price_is_refused() -> None:
    with pytest.raises(CreditDeclarationError) as refusal:
        _load_mutated("priceUsdPerMonth: 20.0", "priceUsdPerMonth: -20.0")
    assert "priceUsdPerMonth" in str(refusal.value)


def test_a_missing_plans_mapping_is_refused() -> None:
    """A provider with no plans at all declares no budget line and is refused.

    Deleting ONE plan is deliberately *not* an error - the remaining plans still
    form a usable budget, and an undeclared plan is refused by name at lookup
    time instead (`test_an_undeclared_plan_is_refused_by_name`).
    """
    with pytest.raises(CreditDeclarationError) as refusal:
        _load_mutated("    plans:", "    plansRenamed:")
    assert "plans" in str(refusal.value)


def test_the_mutation_helper_actually_mutates() -> None:
    """Negative control for the provocation helper itself."""
    with pytest.raises(AssertionError):
        _load_mutated("this string is not in the file", "nor is this")


def test_the_shipped_yaml_is_a_mapping_with_the_declared_providers() -> None:
    """Guard the fixture: a hand-edited file must still parse as intended."""
    document = yaml.safe_load(CREDITS_PATH.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == 1
    assert list(document["providers"]) == ["nous"]
