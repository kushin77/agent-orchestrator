"""Spend against the weekly ceiling — the FinOps half of the roll-up.

One rule dominates every test here: nothing is clamped. A spend above its
ceiling is reported at the value it actually reached, with the excess named and
the SME identified, and the aggregate carries the overspend rather than hiding
it behind a ceiling.
"""

from __future__ import annotations

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_rollup_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
build_report = _conftest.build_report
inventory = _conftest.inventory
org = _conftest.org
sme = _conftest.sme

ONE_TENANT = {"alpha": {"ceiling": 300.0, "repos": ["fx/one"]}}


def test_spend_over_the_ceiling_is_an_explicit_finding(tmp_path):
    report = build_report(
        tmp_path,
        org(ONE_TENANT),
        [inventory("fx/one", "alpha", [sme(id="qa-sme", weekly_spend_ceiling_usd=100.0,
                                           spend_usd=140.0)])],
    )

    over = [f for f in report.findings if f.code == "SPEND_OVER_CEILING"]
    assert len(over) == 1
    assert over[0].severity == "error"
    assert over[0].subject == "fx/one#qa-sme"
    assert "$140.00" in over[0].message and "$100.00" in over[0].message
    assert "over by $40.00" in over[0].message
    assert report.status == "not-ok"
    assert report.exit_code == 1


def test_an_over_ceiling_spend_is_never_clamped_to_the_ceiling(tmp_path):
    report = build_report(
        tmp_path,
        org(ONE_TENANT, ceiling=500.0),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=140.0)])],
    )

    view = report.enterprise.spend
    assert view.total_usd == 140.0  # not 100.0
    assert view.smes_over_ceiling == ("fx/one#qa-sme",)
    fact = report.enterprise.smes[0]
    assert fact.over_ceiling is True
    assert fact.excess_usd == 40.0
    # The SME is over its own ceiling; the enterprise scope is not over its own.
    assert view.excess_usd == 0.0
    assert view.over is False
    assert view.headroom_usd == 360.0


def test_a_scope_breach_is_measured_against_that_scope_ceiling(tmp_path):
    report = build_report(
        tmp_path,
        org({"alpha": {"ceiling": 100.0, "repos": ["fx/one"]}}, ceiling=500.0),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=140.0)])],
    )

    tenant = report.enterprise.tenants[0]
    assert tenant.spend.total_usd == 140.0
    assert tenant.spend.over is True
    assert tenant.spend.excess_usd == 40.0
    assert "TENANT_OVER_CEILING" in [f.code for f in report.findings]
    # ... while the enterprise's own ceiling is still satisfied.
    assert report.enterprise.spend.over is False


def test_spend_exactly_at_the_ceiling_is_not_a_finding(tmp_path):
    report = build_report(
        tmp_path,
        org(ONE_TENANT),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=100.0)])],
    )

    codes = [f.code for f in report.findings]
    assert "SPEND_OVER_CEILING" not in codes
    assert codes == ["SPEND_AT_CEILING"]
    at = report.findings[0]
    assert at.severity == "info"
    assert report.enterprise.spend.total_usd == 100.0
    assert report.enterprise.spend.excess_usd == 0.0
    assert report.status == "ok"
    assert report.exit_code == 0


def test_one_cent_over_the_ceiling_is_over(tmp_path):
    """The boundary is a cent, and it is on the over side of `equal`."""
    report = build_report(
        tmp_path,
        org(ONE_TENANT),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=100.01)])],
    )

    assert [f.code for f in report.findings] == ["SPEND_OVER_CEILING"]
    assert report.findings[0].severity == "error"
    assert report.enterprise.smes[0].excess_usd == 0.01
    assert report.enterprise.spend.smes_over_ceiling == ("fx/one#qa-sme",)
    assert report.status == "not-ok"


def test_the_finding_flips_on_a_single_field_of_one_declaration(tmp_path):
    """NEGATIVE CONTROL: the over-ceiling detection is not a constant.

    The two runs differ in `spend_usd` alone, so a detector that always fired —
    or never fired — could not produce both outcomes.
    """
    tenant = {"alpha": {"ceiling": 300.0, "repos": ["fx/one"]}}
    under = build_report(
        tmp_path / "under",
        org(tenant),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=99.99)])],
    )
    over = build_report(
        tmp_path / "over",
        org(tenant),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=100.01)])],
    )

    assert [f.code for f in under.findings] == []
    assert [f.code for f in over.findings] == ["SPEND_OVER_CEILING"]
    assert under.status == "ok" and under.exit_code == 0
    assert over.status == "not-ok" and over.exit_code == 1


def test_raising_only_the_ceiling_clears_it_without_touching_the_spend(tmp_path):
    tenant = {"alpha": {"ceiling": 300.0, "repos": ["fx/one"]}}
    low = build_report(
        tmp_path / "low",
        org(tenant),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=140.0)])],
    )
    high = build_report(
        tmp_path / "high",
        org(tenant),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=140.0, spend_usd=140.0)])],
    )

    assert "SPEND_OVER_CEILING" in [f.code for f in low.findings]
    assert [f.code for f in high.findings] == ["SPEND_AT_CEILING"]
    assert low.enterprise.spend.total_usd == high.enterprise.spend.total_usd == 140.0


def test_tenant_and_enterprise_ceilings_are_checked_at_their_own_scope(tmp_path):
    tenants = {
        "alpha": {"ceiling": 120.0, "repos": ["fx/one"]},
        "beta": {"ceiling": 300.0, "repos": ["fx/two"]},
    }
    report = build_report(
        tmp_path,
        org(tenants, ceiling=150.0),
        [
            inventory("fx/one", "alpha", [sme(id="qa-sme", weekly_spend_ceiling_usd=90.0,
                                              spend_usd=90.0)]),
            inventory("fx/two", "beta", [sme(id="platform-sme", weekly_spend_ceiling_usd=80.0,
                                             spend_usd=80.0)]),
        ],
    )

    codes = sorted(f.code for f in report.findings)
    # alpha: 90 of 120 (fine). enterprise: 170 of 150 (over, by 20).
    assert "TENANT_OVER_CEILING" not in codes
    assert "ENTERPRISE_OVER_CEILING" in codes
    flagged = next(f for f in report.findings if f.code == "ENTERPRISE_OVER_CEILING")
    assert flagged.subject == "fixture-ent"
    assert "$170.00" in flagged.message and "$150.00" in flagged.message
    assert report.enterprise.spend.excess_usd == 20.0
    assert report.status == "not-ok"
