"""The negative control proves itself: a driver that cannot fail is worthless.

``negative_control.run`` reports which refusals it provoked, so the two
assertions that matter are:

* every code in the lane's vocabulary was provoked *and* refused by name;
* the driver goes red when a control it reports is neutered — the same mutant
  ``scripts/check-erp-finops.sh`` applies to a copied tree, applied here with
  ``monkeypatch`` so the suite itself cannot pass with a dead control.
"""

from __future__ import annotations

from integrations.erp.finops import negative_control
from integrations.erp.finops.budget import ErpBudgetGuard
from integrations.erp.finops.model import REFUSALS
from integrations.erp.finops.rollup import ErpRollup


def test_every_declared_refusal_is_provoked_and_named() -> None:
    result = negative_control.run(capture=True)
    assert result.failures == ()
    assert result.uncovered == ()
    assert set(result.codes) == set(REFUSALS)


def test_the_driver_goes_red_when_the_budget_stop_is_neutered(monkeypatch) -> None:
    monkeypatch.setattr(ErpBudgetGuard, "guard", lambda self, *args, **kwargs: None)
    result = negative_control.run(capture=True)
    assert not result.ok
    assert "budget-exhausted" in result.uncovered


def test_the_driver_goes_red_when_the_unmetered_refusal_is_neutered(monkeypatch) -> None:
    monkeypatch.setattr(ErpRollup, "bill", lambda self, tenant, month=None: None)
    result = negative_control.run(capture=True)
    assert not result.ok
    assert "unmetered-usage" in result.uncovered


def test_a_provocation_that_refuses_for_the_wrong_reason_is_a_finding(monkeypatch) -> None:
    """The offender must be named: "something was refused" is not evidence."""

    from integrations.erp.finops import rates
    from integrations.erp.finops.model import Refused

    def wrong_detail(self, kind, operation):
        raise Refused("rate-missing", "no rate", where=self.source)

    monkeypatch.setattr(rates.RateCard, "rate_for", wrong_detail)
    result = negative_control.run(capture=True)
    assert not result.ok
    assert any("without naming" in failure for failure in result.failures)
