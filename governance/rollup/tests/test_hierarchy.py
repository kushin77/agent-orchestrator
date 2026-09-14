"""The hierarchy: repos roll up to tenants, tenants to the enterprise.

The aggregate is only worth anything if it is *arithmetic over the declarations*
— so these tests assert the totals against hand-computed sums, and assert the
roll-up moves by exactly the delta when a single declaration moves. That second
assertion is what separates a computed roll-up from a narrated one.
"""

from __future__ import annotations

from conftest import build_report, inventory, org, sme

TENANTS = {
    "alpha": {"ceiling": 500.0, "repos": ["fx/one", "fx/two"]},
    "beta": {"ceiling": 300.0, "repos": ["fx/three"]},
}


def fleets():
    """Three repos over two tenants; `qa-sme` deliberately appears twice."""
    return [
        inventory(
            "fx/one",
            "alpha",
            [
                sme(id="qa-sme", spend_usd=40.0, engaged_hours=10.0, capacity_hours=20.0,
                    dispatched=4, closed=3),
                sme(id="iac-sme", spend_usd=25.0, engaged_hours=6.0, capacity_hours=12.0,
                    dispatched=2, closed=2),
            ],
        ),
        inventory(
            "fx/two",
            "alpha",
            [
                sme(id="qa-sme", spend_usd=30.0, engaged_hours=8.0, capacity_hours=16.0,
                    dispatched=3, closed=1),
            ],
            findings=1,
        ),
        inventory(
            "fx/three",
            "beta",
            [
                sme(id="platform-sme", spend_usd=50.0, engaged_hours=9.0, capacity_hours=18.0,
                    dispatched=5, closed=4),
            ],
        ),
    ]


def test_repo_rolls_up_to_its_tenant_and_the_tenant_to_the_enterprise(tmp_path):
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), fleets())

    by_id = {t.id: t for t in report.enterprise.tenants}
    assert by_id["alpha"].repos == ("fx/one", "fx/two")
    assert by_id["beta"].repos == ("fx/three",)

    # Hands-computed: one = 40 + 25, two = 30, three = 50.
    assert by_id["alpha"].spend.total_usd == 95.0
    assert by_id["beta"].spend.total_usd == 50.0
    assert report.enterprise.spend.total_usd == 145.0
    assert report.enterprise.spend.total_usd == sum(
        t.spend.total_usd for t in report.enterprise.tenants
    )
    assert report.status == "ok"
    assert report.exit_code == 0


def test_every_declared_repo_reaches_the_rollup_with_its_own_totals(tmp_path):
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), fleets())

    per_repo = {f.repo: f for f in report.fleets}
    assert sorted(per_repo) == ["fx/one", "fx/three", "fx/two"]
    assert per_repo["fx/one"].smes[0].repo == "fx/one"
    assert report.enterprise.repos == ("fx/one", "fx/two", "fx/three")
    assert report.enterprise.missing_repos == ()


def test_sme_inventory_is_repo_scoped_so_the_same_sme_id_is_two_facts(tmp_path):
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), fleets())

    inventory_view = report.enterprise.to_dict()["sme_inventory"]
    assert inventory_view["count"] == 4
    assert inventory_view["by_repo"] == {
        "fx/one": ["iac-sme", "qa-sme"],
        "fx/three": ["platform-sme"],
        "fx/two": ["qa-sme"],
    }
    refs = [s.ref for s in report.enterprise.smes]
    assert refs.count("fx/one#qa-sme") == 1
    assert refs.count("fx/two#qa-sme") == 1


def test_utilisation_closure_and_drift_are_sums_of_the_declarations(tmp_path):
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), fleets())

    enterprise = report.enterprise
    # engaged 10+6+8+9 = 33 over capacity 20+12+16+18 = 66.
    assert enterprise.utilization.numerator == 33.0
    assert enterprise.utilization.denominator == 66.0
    assert enterprise.utilization.ratio == 0.5
    # closed 3+2+1+4 = 10 of dispatched 4+2+3+5 = 14.
    assert (enterprise.closure.numerator, enterprise.closure.denominator) == (10, 14)
    assert enterprise.closure.ratio == 10 / 14
    # drift findings 0+1+0 = 1 of checks 4+4+4 = 12.
    assert (enterprise.drift.numerator, enterprise.drift.denominator) == (1, 12)
    assert enterprise.drift.ratio == 1 / 12


def test_changing_one_declaration_moves_the_aggregate_by_exactly_that_delta(tmp_path):
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), fleets())
    assert report.enterprise.spend.total_usd == 145.0

    raised = fleets()
    raised[0]["smes"][0]["spend_usd"] = 47.25  # fx/one#qa-sme: 40.00 -> 47.25
    after = build_report(tmp_path / "raised", org(TENANTS, ceiling=1200.0), raised)

    assert after.enterprise.spend.total_usd == 152.25
    assert after.enterprise.spend.total_usd - report.enterprise.spend.total_usd == 7.25
    alpha = next(t for t in after.enterprise.tenants if t.id == "alpha")
    beta = next(t for t in after.enterprise.tenants if t.id == "beta")
    assert alpha.spend.total_usd == 102.25
    assert beta.spend.total_usd == 50.0


def test_one_tenants_change_leaves_the_other_tenants_scope_alone(tmp_path):
    """Isolation: a tenant's totals answer only for its own repos."""
    base = build_report(tmp_path / "base", org(TENANTS, ceiling=1200.0), fleets())
    moved = fleets()
    moved[2]["smes"][0]["spend_usd"] = 55.0  # fx/three, tenant beta
    after = build_report(tmp_path / "after", org(TENANTS, ceiling=1200.0), moved)

    alpha_before = next(t for t in base.enterprise.tenants if t.id == "alpha")
    alpha_after = next(t for t in after.enterprise.tenants if t.id == "alpha")
    assert alpha_after.spend.total_usd == alpha_before.spend.total_usd == 95.0
    beta_after = next(t for t in after.enterprise.tenants if t.id == "beta")
    assert beta_after.spend.total_usd == 55.0
    assert after.enterprise.spend.total_usd == 150.0


def test_two_repos_do_not_bleed_into_each_others_totals(tmp_path):
    """The same SME id in two repos stays two disjoint contributions."""
    moved = fleets()
    # fx/one#qa-sme keeps its spend; fx/two#qa-sme is re-declared far higher.
    moved[1]["smes"][0]["spend_usd"] = 300.0
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), moved)

    per_repo = {f.repo: f for f in report.fleets}
    one_total = sum(s.spend_usd for s in per_repo["fx/one"].smes)
    two_total = sum(s.spend_usd for s in per_repo["fx/two"].smes)
    assert one_total == 65.0
    assert two_total == 300.0
    alpha = next(t for t in report.enterprise.tenants if t.id == "alpha")
    assert alpha.spend.total_usd == 365.0
    assert alpha.spend.total_usd == one_total + two_total


def test_the_orgs_declaration_defines_the_scope_of_the_view(tmp_path):
    """An inventory the org does not list is out of scope, and says so."""
    extra = fleets() + [
        inventory("fx/rogue", "alpha", [sme(id="platform-sme", spend_usd=999.0)])
    ]
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), extra)

    codes = [f.code for f in report.findings]
    assert "REPO_UNKNOWN" in codes
    assert report.enterprise.spend.total_usd == 145.0  # the rogue repo contributes nothing
    assert report.status == "not-ok"
    assert report.exit_code == 1
