"""PF-5: EPIC #665's bridge + cache-optimal inference loop (issue #1019).

The probe lives in ``e2e/finops_loop.py``; this suite asserts what it measured.
The subject is the epic's own name — *from CRM hook to ledger* — plus the token
half of the same chain: a CRM conversion event crossing the webhook bridge into
the general ledger, and the inference that answers it being served
cache-optimally and metered against the rate cards.

Every assertion below is either a fact measured over the merged modules or a
check that a guard genuinely refused when provoked.  The probe is deterministic
and keyless, so it runs once per module and each test reads a different fact
about that one run — exactly as the sibling
``e2e/tests/test_erp_finops_golden.py`` does for the money chain.

The seven negative controls are the part that could silently rot: each names
the branch a mutation must disable, and ``test_every_control_names_its_refusal_
and_its_mutant`` checks that every named target is a test that exists in this
module.  The mutation itself is driven from OUTSIDE the repo
(``/tmp/ao1019-mutate.sh``: it copies the module, removes the named branch, and
re-runs the control's own test, which must then FAIL) — nothing is patched in
place.
"""

from __future__ import annotations

import json

import pytest

from e2e.finops_loop import (
    CALL_CLASS,
    CALLS,
    CONVERSION_AMOUNT,
    CRM_CLOCK,
    DELTA_TOKENS,
    EVENT_ID,
    FORGED_SIGNING_KEY,
    INCOME_ACCOUNT,
    MODEL,
    OUTPUT_TOKENS,
    PREFIX_TOKENS,
    PROVIDER,
    RECEIVABLE_ACCOUNT,
    SESSION_STARTED,
    TENANT,
    TRIM_TOOL,
    WEBHOOK_SIGNING_KEY,
    canonical_prompt,
    probe_controls,
    probe_loop,
    rate_cards,
    run_loop,
)
from e2e.finops_loop import _prefix_accepted


@pytest.fixture(scope="module")
def loop():
    """One full run: conversion -> ledger, inference -> metered cost, trim."""
    return probe_loop()


@pytest.fixture(scope="module")
def controls():
    """The seven negative controls, each driven through the owning module."""
    return probe_controls()


def _control(controls, control_id):
    return next(
        control for control in controls["controls"] if control["controlId"] == control_id
    )


# --------------------------------------------------------------------------- #
# the chain is green
# --------------------------------------------------------------------------- #
def test_no_stage_reported_a_failure(loop):
    """Every stage's own failure list is empty."""
    assert loop.failures() == (), loop.failures()


def test_the_whole_loop_and_its_controls_are_green():
    """The single composite verdict the probe publishes, controls included."""
    payload = run_loop()
    assert payload["failures"] == [], payload["failures"]
    assert payload["passed"] is True


# --------------------------------------------------------------------------- #
# AC 1 — the bridge posts a conversion to the ledger
# --------------------------------------------------------------------------- #
def test_the_conversion_event_posts_a_balanced_ledger_document(loop):
    """AC 1: a signed CRM conversion crosses the bridge into a real balance.

    The amount and the voucher are read back through the modules that own them:
    the event's own parse (``webhooks.schema``) and the posting itself
    (``integrations.erp.tx.ledger.GeneralLedger``) — neither is re-derived here,
    and no figure below is one this suite chose.
    """
    conversion = loop.conversion
    assert conversion["signatureVerified"] is True
    assert conversion["signatureHeaderPrefix"] == "sha256"

    result = conversion["result"]
    assert result["status"] == "posted"
    assert result["entryCount"] == 2
    assert result["reason"] is None

    # the event is the CRM lane's own: it cites the won opportunity's ids
    event = conversion["event"]
    assert event["tenant"] == TENANT
    assert event["occurredAt"] == CRM_CLOCK["won"]
    assert event["sourceDocument"].startswith("opportunity/")
    assert conversion["crmCustomerKind"] == "customer"
    assert conversion["crmCustomerState"] == "active"

    posting = conversion["posting"]
    assert posting["readBackThrough"] == "integrations.erp.tx.ledger.GeneralLedger"
    assert len(posting["rows"]) == 2
    assert posting["verify"] == []
    assert posting["debitTotal"] == posting["creditTotal"] != 0
    assert posting["netForVoucher"] == 0
    # exactly one debit and one credit, and the debit is the event's own amount
    debit_rows = [row for row in posting["rows"] if row["debit"]]
    credit_rows = [row for row in posting["rows"] if row["credit"]]
    assert len(debit_rows) == len(credit_rows) == 1
    assert debit_rows[0]["debit"] == credit_rows[0]["credit"] == event["amount"]
    assert event["amount"] == CONVERSION_AMOUNT
    # the voucher is the bridge's own construction, not one this suite wrote
    assert conversion["voucherId"] == "crm-conversion:{}".format(event["eventId"])
    assert conversion["voucherId"] == result["voucherId"]


def test_the_ledger_holds_exactly_the_posted_voucher(loop):
    """The read-back is the whole ledger, and it holds one voucher's two rows."""
    conversion = loop.conversion
    posting = conversion["posting"]
    assert conversion["ledgerRows"] == 2
    assert [row["voucherId"] for row in posting["rows"]] == [conversion["voucherId"]] * 2
    assert [row["voucherType"] for row in posting["rows"]] == ["crm-conversion"] * 2
    # the accounts and their balances are the ledger's own, and they are the
    # chart of accounts the posting policy declares
    assert set(posting["accounts"]) == {RECEIVABLE_ACCOUNT, INCOME_ACCOUNT}
    assert sorted(posting["balances"]) == sorted(posting["accounts"])
    assert posting["balances"][RECEIVABLE_ACCOUNT] == conversion["event"]["amount"]
    assert posting["balances"][INCOME_ACCOUNT] == -conversion["event"]["amount"]
    # the timestamp the rows carry is the injected clock's, never the wall's
    assert {row["at"] for row in posting["rows"]} == {CRM_CLOCK["won"]}
    # a replayed delivery returned the first outcome and moved nothing
    assert conversion["replay"]["status"] == "duplicate"
    assert conversion["replay"]["voucherId"] == conversion["voucherId"]


def test_the_token_half_is_joined_to_the_accounting_half(loop):
    """The chain is one chain: the usage axis is the voucher the bridge minted."""
    inference = loop.inference
    assert inference["tenant"] == TENANT == loop.conversion["event"]["tenant"]
    assert inference["billingQueryId"] == loop.conversion["voucherId"]
    assert inference["warm"]["perQuery"] == [loop.conversion["voucherId"]]
    assert inference["warm"]["hasBillingQuery"] is True
    assert inference["warm"]["joinLine"] == "join: billing-query ids present"


# --------------------------------------------------------------------------- #
# AC 2 and AC 3 — the bridge's refusal paths
# --------------------------------------------------------------------------- #
def test_the_flag_off_bridge_refuses_and_posts_nothing(controls):
    """AC 2: with ``erp-webhooks-bridge`` off the bridge refuses, measured."""
    control = _control(controls, "flag-off-refuses-every-delivery")
    evidence = control["evidence"]
    assert control["refusedBy"] == "quarantined"
    assert evidence["defaultEnabled"] is False
    assert evidence["isEnabledWithNoFlags"] is False
    assert evidence["isEnabledWithFlagOff"] is False
    assert evidence["status"] == "quarantined"
    assert evidence["reasonNamesTheFlag"] is True
    # nothing was posted, nothing was recorded, and the attempt is audited
    assert evidence["ledgerRows"] == 0
    assert evidence["idempotencyKeys"] == 0
    assert evidence["quarantinedEvents"] == 1
    # the pairing: the same delivery with the flag on DOES post
    assert evidence["pairedAcceptedStatus"] == "posted"
    assert evidence["pairedLedgerRows"] == 2


def test_a_forged_signature_is_refused_and_posts_nothing(controls):
    """AC 3: a forged signature is refused by name, and nothing is posted."""
    control = _control(controls, "forged-signature-refused-nothing-posted")
    evidence = control["evidence"]
    assert control["refusedBy"] == "auth-failed"
    assert evidence["verifyRaised"] == "auth-failed"
    assert evidence["unlabeledHeaderRaised"] == "auth-failed"
    assert evidence["absentHeaderRaised"] == "auth-failed"
    assert evidence["status"] == "quarantined"
    assert evidence["reasonPrefix"] == "auth-failed"
    assert evidence["ledgerRows"] == 0
    assert evidence["idempotencyKeys"] == 0
    assert evidence["quarantinedEvents"] == 1
    # the pairing: the honest signature posts
    assert evidence["pairedAcceptedStatus"] == "posted"
    assert evidence["pairedLedgerRows"] == 2
    # the forgery used a different key, so the refusal is the verification's
    assert FORGED_SIGNING_KEY != WEBHOOK_SIGNING_KEY


def test_a_replayed_delivery_does_not_post_twice(controls):
    """A replayed delivery returns the first outcome; the ledger does not move."""
    control = _control(controls, "replayed-delivery-posts-once")
    evidence = control["evidence"]
    assert evidence["firstStatus"] == "posted"
    assert evidence["secondStatus"] == "duplicate"
    assert evidence["sameVoucher"] is True
    assert evidence["ledgerRowsAfterFirst"] == 2
    assert evidence["ledgerRowsAfterReplay"] == 2
    assert evidence["idempotencyKeys"] == 1
    assert evidence["quarantinedEvents"] == 0


# --------------------------------------------------------------------------- #
# AC 4 — the token half: cache-optimal inference, metered
# --------------------------------------------------------------------------- #
def test_the_cached_inference_reports_a_hit_share_and_costs_less(loop):
    """AC 4: the same scenario, canonical vs not: hits up, metered cost down."""
    inference = loop.inference
    warm = inference["warm"]
    cold = inference["cold"]

    # the canonical composition is cacheable: one footprint, every call accepted
    assert warm["disposition"]["warm"] is True
    assert warm["disposition"]["acceptedByPrefixValidator"] == CALLS
    assert warm["disposition"]["distinctFootprints"] == 1
    assert warm["prefixAcceptedEveryCall"] is True

    # the cold composition is not, and that is what makes the split fail-able
    assert cold["disposition"]["warm"] is False
    assert cold["disposition"]["acceptedByPrefixValidator"] == 0
    assert cold["disposition"]["distinctFootprints"] == CALLS

    assert warm["cacheHitRatio"] > 0
    assert cold["cacheHitRatio"] == 0
    assert warm["recordedHitTokens"] == PREFIX_TOKENS * (CALLS - 1)
    assert cold["recordedHitTokens"] == 0
    # the same scenario in both arms: the same prompt tokens, only the billed
    # side of the split differs
    assert warm["promptTokens"] == cold["promptTokens"]
    assert warm["recordedMissTokens"] + warm["recordedHitTokens"] == (
        cold["recordedMissTokens"]
    )

    # strictly lower, and by exactly the tokens the cache saved
    assert warm["costUsd"] < cold["costUsd"]
    assert inference["savedTokens"] == (
        cold["recordedMissTokens"] - warm["recordedMissTokens"]
    )
    assert inference["savedTokens"] == PREFIX_TOKENS * (CALLS - 1)


def test_the_cache_split_comes_from_the_prefix_module_not_a_constant(loop):
    """The warm/cold decision is the prefix module's, so it can be wrong."""
    voucher = loop.conversion["voucherId"]
    canonical = [canonical_prompt(index, voucher) for index in range(CALLS)]
    # every canonical call passes the validator ...
    assert all(_prefix_accepted(prompt) for prompt in canonical)

    warm = loop.inference["warm"]["disposition"]
    cold = loop.inference["cold"]["disposition"]
    # ... and normalises to ONE cache key, while the cold arm has one per call,
    # so the two arms cannot collide
    assert len(set(warm["footprints"])) == 1
    assert len(set(cold["footprints"])) == CALLS
    assert not set(warm["footprints"]) & set(cold["footprints"])


# --------------------------------------------------------------------------- #
# AC 6 — the cost is not re-derived
# --------------------------------------------------------------------------- #
def test_every_cost_figure_comes_from_the_rate_cards(loop):
    """AC 6: every figure equals the rate-card computation, never a literal."""
    inference = loop.inference
    cards = rate_cards()
    assert inference["rateCard"]["source"] == "deepseek.yaml"

    for name in ("warm", "cold"):
        stage = inference[name]
        assert stage["costSources"] == ["rate_card"]
        assert stage["everyCallMatchesRateCard"] is True
        for call in stage["calls"]:
            expected = cards.estimate(PROVIDER, MODEL, call["inputTokens"], OUTPUT_TOKENS)
            assert call["costUsd"] == expected.cost_usd, (name, call["inputTokens"])
            assert call["costUsdFromRateCard"] == expected.cost_usd
        assert stage["costUsd"] == stage["costUsdFromRateCards"]

    # the saving equals the rate card's own price for the cached tokens
    assert inference["costGapMatchesRateCard"] is True
    assert inference["costGapFromRateCardUsd"] == cards.estimate(
        PROVIDER, MODEL, inference["savedTokens"], 0
    ).cost_usd
    # the #667 baseline prices from the same cards and agrees the path is cheaper
    baseline = inference["baseline"]
    assert baseline["standardMatchesRateCard"] is True
    assert baseline["cacheHitRatio"] == inference["warm"]["cacheHitRatio"]
    assert baseline["cacheAdjustmentCheaper"] is True


# --------------------------------------------------------------------------- #
# AC 5 — the MCP filter trims before the model sees it
# --------------------------------------------------------------------------- #
def test_the_mcp_filter_trims_before_the_model_sees_it(loop):
    """AC 5: the model-visible envelope is strictly smaller than the unfiltered one."""
    trim = loop.trim
    assert trim["payloadChars"] > trim["budgetChars"]  # the fixture is verbose
    assert trim["trimmedChars"] < trim["payloadChars"]
    assert trim["insideBudget"] is True
    assert trim["markerPresent"] is True
    assert trim["keySectionsSurvived"] is True
    # the bytes the model is handed are strictly smaller ...
    assert trim["modelVisibleTrimmedChars"] < trim["modelVisibleChars"]
    assert trim["smallerBeforeTheModel"] is True
    # ... and the shrinkage is the filter's: disabled, it passes byte-identically
    assert trim["disabledPassesByteIdentical"] is True
    assert trim["tool"] == TRIM_TOOL


def test_the_mcp_filter_refuses_an_unbudgeted_tool(controls):
    """The rule registry is closed: an undeclared tool is refused by name."""
    from mcp.response_filter import ResponseFilter

    control = _control(controls, "undeclared-tool-refused-fail-closed")
    evidence = control["evidence"]
    assert control["refusedBy"] == "UnknownRuleError"
    assert evidence["hasRule"] is False
    assert evidence["refused"] is True
    assert evidence["refusalNamesTheTool"] is True
    # the pairing: a declared tool IS filtered, to its own declared budget
    assert evidence["pairedToolFilteredChars"] <= (
        ResponseFilter(enabled=True).rules()[TRIM_TOOL].budget_chars
    )


def test_a_deviating_prefix_is_refused_by_name(controls):
    """A composed prompt whose prefix deviates is refused, naming the class."""
    control = _control(controls, "deviating-prefix-refused-by-name")
    evidence = control["evidence"]
    assert control["refusedBy"] == "prefix-deviation"
    assert CALL_CLASS in control["detail"]
    assert evidence["canonicalAccepted"] is True
    assert evidence["deviatingRefused"] is True
    assert evidence["refusalNamesTheClass"] is True
    assert evidence["unknownClassRefused"] is True
    assert evidence["footprintsDiffer"] is True


# --------------------------------------------------------------------------- #
# the token half's honesty paths
# --------------------------------------------------------------------------- #
def test_an_absent_cache_split_is_cannot_assess_never_a_fabricated_hit(controls):
    """No cache headers on a record means CANNOT-ASSESS, never a fabricated hit."""
    control = _control(controls, "absent-cache-split-is-cannot-assess")
    evidence = control["evidence"]
    assert control["refusedBy"] == "CANNOT-ASSESS"
    assert evidence["absentSplit"] == [None, None]
    assert evidence["presentSplit"] == [PREFIX_TOKENS, DELTA_TOKENS]
    assert evidence["absentRatio"] is None
    assert evidence["absentCacheAdjustedCost"] is None
    # the standard cost is still priced from the card, so the refusal is the
    # cache split's and not a card that cannot price the model
    assert evidence["absentStandardCostIsPriced"] is True


def test_an_absent_join_key_is_reported_never_fabricated(controls):
    """With no billing-query axis the audit says so, and invents no per-query row."""
    control = _control(controls, "absent-join-key-reported-never-fabricated")
    evidence = control["evidence"]
    assert control["refusedBy"] == "no billing-query ids present"
    assert evidence["withoutAxisHasJoin"] is False
    assert evidence["withoutAxisPerQuery"] == []
    assert evidence["withoutAxisJoinLine"] == "join: no billing-query ids present"
    # the aggregate row survives, so the baseline stays regressable ...
    assert evidence["withoutAxisTotalHitTokens"] == PREFIX_TOKENS * CALLS
    # ... and the pairing shows the axis is real when a record carries one
    assert evidence["withAxisPerQuery"] == ["crm-conversion:{}".format(EVENT_ID)]


# --------------------------------------------------------------------------- #
# AC 7 — each refusal is named, and mutation-proved by name
# --------------------------------------------------------------------------- #
def test_every_control_names_its_refusal_and_its_mutant(controls):
    """AC 7: every control names its refusal AND the branch a mutant must remove.

    The mutation itself runs outside the repo, so what this test guarantees is
    the contract it depends on: each control reports a named refusal, and its
    declared ``mustFail`` target is a test that really exists in this module
    (an undeclared or misspelled target would make the driver green by
    accident).  Distinct mutant names keep the proofs from collapsing into one.
    """
    assert controls["failedControls"] == [], controls["failedControls"]
    assert controls["passed"] is True
    assert len(controls["controls"]) == 7

    here = set(globals())
    mutants = {}
    for control in controls["controls"]:
        assert control["passed"] is True, control["controlId"]
        assert control["refusedBy"], control["controlId"]
        mutant = control["mutant"]
        assert set(mutant) == {"name", "module", "branch", "mustFail"}, control["controlId"]
        assert mutant["mustFail"] in here, mutant["mustFail"]
        assert mutant["mustFail"].startswith("test_"), mutant["mustFail"]
        mutants[mutant["name"]] = mutant
    assert len(mutants) == 7, sorted(mutants)


def test_each_control_is_paired_with_the_opposite_measurement(controls):
    """A guard that refused everything, or nothing, could not have passed."""
    flag_off = _control(controls, "flag-off-refuses-every-delivery")["evidence"]
    assert flag_off["ledgerRows"] == 0 and flag_off["pairedLedgerRows"] == 2

    forged = _control(controls, "forged-signature-refused-nothing-posted")["evidence"]
    assert forged["ledgerRows"] == 0 and forged["pairedLedgerRows"] == 2

    prefix = _control(controls, "deviating-prefix-refused-by-name")["evidence"]
    assert prefix["deviatingRefused"] is True and prefix["canonicalAccepted"] is True

    tool = _control(controls, "undeclared-tool-refused-fail-closed")["evidence"]
    assert tool["refused"] is True and tool["pairedToolFilteredChars"] > 0


# --------------------------------------------------------------------------- #
# AC 8 — no date bomb
# --------------------------------------------------------------------------- #
def test_the_probe_is_a_function_of_the_scenario_not_the_clock():
    """AC 8: two runs, one answer — every clock injected, none read.

    ``telemetry.metering.parse_ts(None)`` falls back to the wall clock, so a
    record handed to the intake without a stamp would make the cost a function
    of when the suite ran.  The probe always injects one, the injected stamp
    comes back verbatim, and two independent runs agree byte for byte.
    """
    first = run_loop()
    second = run_loop()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    inference = first["inference"]
    for name in ("warm", "cold"):
        stage = inference[name]
        assert [call["ts"] for call in stage["calls"]] == list(SESSION_STARTED)
        assert all(call["ts"] in SESSION_STARTED for call in stage["calls"])

    # the accounting half carries the injected clock too
    conversion = first["conversion"]
    assert conversion["event"]["occurredAt"] == CRM_CLOCK["won"]
    assert {row["at"] for row in conversion["posting"]["rows"]} == {CRM_CLOCK["won"]}
