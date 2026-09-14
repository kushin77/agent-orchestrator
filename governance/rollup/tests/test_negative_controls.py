"""Negative controls: the aggregate and the findings are not vacuous (issue #151).

A roll-up that always reports the same summary, or never reports a violation,
would pass every positivity test in the other modules. Each test here names the
mutation it is designed to catch — the `presence`/`absence` pairs are what make
a gate able to fail, and the last two prove the two scopes stay disjoint.
"""

from __future__ import annotations

from conftest import build_report, inventory, org, sme

TENANTS = {
    "alpha": {"ceiling": 500.0, "repos": ["fx/one", "fx/two"]},
    "beta": {"ceiling": 300.0, "repos": ["fx/three"]},
}


def fleets(spend_one=40.0, spend_two=30.0, spend_three=50.0):
    return [
        inventory("fx/one", "alpha", [sme(id="qa-sme", spend_usd=spend_one)]),
        inventory("fx/two", "alpha", [sme(id="qa-sme", spend_usd=spend_two)]),
        inventory("fx/three", "beta", [sme(id="platform-sme", spend_usd=spend_three)]),
    ]


def test_the_aggregate_is_not_a_constant_or_a_single_repo_in_disguise(tmp_path):
    """Catches `sum(...)` mutated to a literal, to zero, or to one repo's value."""
    report = build_report(tmp_path, org(TENANTS, ceiling=1200.0), fleets())
    total = report.enterprise.spend.total_usd
    per_repo = {
        fleet.repo: sum(s.spend_usd for s in fleet.smes) for fleet in report.fleets
    }

    assert total == 120.0
    assert total != 0.0
    assert total not in per_repo.values()
    assert total == sum(per_repo.values())
    assert len(per_repo) == 3


def test_every_repo_in_scope_moves_the_aggregate(tmp_path):
    """Catches an aggregation that silently drops a repo."""
    base = build_report(tmp_path / "base", org(TENANTS, ceiling=1200.0), fleets())
    for index, repo in enumerate(("fx/one", "fx/two", "fx/three")):
        bumped = fleets()
        bumped[index]["smes"][0]["spend_usd"] += 1.0
        after = build_report(
            tmp_path / ("bump-%d" % index), org(TENANTS, ceiling=1200.0), bumped
        )
        assert after.enterprise.spend.total_usd - base.enterprise.spend.total_usd == 1.0, repo


def test_narrowing_the_org_narrows_the_view_by_exactly_the_removed_repo(tmp_path):
    """Catches a scope that ignores the org declaration and aggregates everything."""
    wider = build_report(tmp_path / "wide", org(TENANTS, ceiling=1200.0), fleets())
    narrower = build_report(
        tmp_path / "narrow",
        org(
            {"alpha": {"ceiling": 500.0, "repos": ["fx/one", "fx/two"]}},
            ceiling=1200.0,
        ),
        fleets(),
    )

    assert wider.enterprise.spend.total_usd == 120.0
    assert narrower.enterprise.spend.total_usd == 70.0
    assert wider.enterprise.spend.total_usd - narrower.enterprise.spend.total_usd == 50.0
    assert "fx/three" in wider.enterprise.repos
    assert "fx/three" not in narrower.enterprise.repos


def test_a_duplicated_sme_is_excluded_and_reported(tmp_path):
    """Catches a duplicate that inflates the total without saying so."""
    counted_twice = build_report(
        tmp_path / "twice",
        org({"alpha": {"ceiling": 500.0, "repos": ["fx/one"]}}, ceiling=1200.0),
        [
            inventory(
                "fx/one",
                "alpha",
                [sme(id="qa-sme", spend_usd=40.0), sme(id="qa-sme", spend_usd=40.0)],
            )
        ],
    )
    once = build_report(
        tmp_path / "once",
        org({"alpha": {"ceiling": 500.0, "repos": ["fx/one"]}}, ceiling=1200.0),
        [inventory("fx/one", "alpha", [sme(id="qa-sme", spend_usd=40.0)])],
    )

    assert "SME_DUPLICATE" in [f.code for f in counted_twice.findings]
    assert counted_twice.status == "not-ok"
    assert counted_twice.enterprise.spend.total_usd == once.enterprise.spend.total_usd == 40.0
    assert len(counted_twice.enterprise.smes) == 1


def test_an_inventory_the_org_does_not_declare_contributes_nothing(tmp_path):
    report = build_report(
        tmp_path,
        org({"alpha": {"ceiling": 500.0, "repos": ["fx/one"]}}, ceiling=1200.0),
        [
            inventory("fx/one", "alpha", [sme(id="qa-sme", spend_usd=40.0)]),
            inventory("fx/two", "alpha", [sme(id="qa-sme", spend_usd=40.0)]),
        ],
    )

    # fx/two is declared by no tenant: it is out of scope, and the scope says so.
    assert "REPO_UNKNOWN" in [f.code for f in report.findings]
    assert report.enterprise.spend.total_usd == 40.0
    assert report.status == "not-ok"


def test_two_inventory_files_for_one_repo_are_refused_rather_than_summed(tmp_path):
    """Catches a second declaration being folded into the first in silence."""
    import yaml

    from conftest import write_tree

    org_doc = org({"alpha": {"ceiling": 500.0, "repos": ["fx/one"]}}, ceiling=1200.0)
    write_tree(tmp_path, org_doc, [inventory("fx/one", "alpha", [sme(spend_usd=40.0)])])
    # A second file for the SAME repo, which write_tree would otherwise overwrite.
    (tmp_path / "inventory" / "fx-one-again.yaml").write_text(
        yaml.safe_dump(
            inventory("fx/one", "alpha", [sme(spend_usd=40.0)]), sort_keys=False
        ),
        encoding="utf-8",
    )

    from pathlib import Path

    from conftest import ROLLUP_DIR

    from inputs import load_inputs
    from model import project

    loaded = load_inputs(tmp_path / "org.yaml", tmp_path / "inventory", ROLLUP_DIR / "schema.yaml")
    assert loaded.org is not None
    report = project(loaded.org, loaded.fleets, inputs=loaded.inputs)

    assert "REPO_DUPLICATED" in [f.code for f in report.findings]
    assert report.enterprise.spend.total_usd == 40.0  # counted once, not 80.0
    assert report.status == "not-ok"
    assert Path(tmp_path / "inventory" / "fx-one-again.yaml").exists()


def test_a_repo_under_two_tenants_is_an_isolation_violation(tmp_path):
    doubled = {
        "alpha": {"ceiling": 500.0, "repos": ["fx/one", "fx/shared"]},
        "beta": {"ceiling": 300.0, "repos": ["fx/shared"]},
    }
    report = build_report(
        tmp_path,
        org(doubled, ceiling=1200.0),
        [
            inventory("fx/one", "alpha", [sme(spend_usd=40.0)]),
            inventory("fx/shared", "alpha", [sme(spend_usd=25.0)]),
        ],
    )

    duplicated = [f for f in report.findings if f.code == "REPO_DUPLICATED"]
    assert len(duplicated) == 1
    assert duplicated[0].subject == "fx/shared"
    assert "first tenant only" in duplicated[0].message
    assert "unassessable" in duplicated[0].message
    assert report.enterprise.spend.total_usd == 65.0  # counted once, not twice
    # The second tenant holds a repo with no facts of its own, so the view cannot
    # be asserted: NOT-OK would claim we checked a scope we could not read.
    assert report.status == "cannot-assess"
    assert report.exit_code == 2


def test_an_utilisation_over_capacity_is_reported_as_measured_never_clamped(tmp_path):
    report = build_report(
        tmp_path,
        org({"alpha": {"ceiling": 500.0, "repos": ["fx/one"]}}, ceiling=1200.0),
        [
            inventory(
                "fx/one",
                "alpha",
                [sme(capacity_hours=20.0, engaged_hours=30.0)],
            )
        ],
    )

    assert "UTILIZATION_OVER_CAPACITY" in [f.code for f in report.findings]
    assert report.enterprise.utilization.ratio == 1.5  # not 1.0
    assert report.enterprise.smes[0].utilization == 1.5
    assert report.status == "not-ok"
    assert report.exit_code == 1


def test_a_closure_rate_above_one_hundred_percent_is_reported_not_clamped(tmp_path):
    report = build_report(
        tmp_path,
        org({"alpha": {"ceiling": 500.0, "repos": ["fx/one"]}}, ceiling=1200.0),
        [inventory("fx/one", "alpha", [sme(dispatched=4, closed=10)])],
    )

    assert "CLOSURE_INCONSISTENT" in [f.code for f in report.findings]
    assert report.enterprise.closure.ratio == 2.5
    assert report.status == "not-ok"


def test_a_repo_filed_under_the_wrong_tenant_is_refused(tmp_path):
    """Catches an inventory silently landing in a tenant that did not claim it."""
    report = build_report(
        tmp_path,
        org(TENANTS, ceiling=1200.0),
        [
            inventory("fx/one", "beta", [sme(spend_usd=40.0)]),
            inventory("fx/two", "alpha", [sme(spend_usd=30.0)]),
            inventory("fx/three", "beta", [sme(id="platform-sme", spend_usd=50.0)]),
        ],
    )

    mismatched = [f for f in report.findings if f.code == "TENANT_MISMATCH"]
    assert [f.subject for f in mismatched] == ["fx/one"]
    alpha = next(t for t in report.enterprise.tenants if t.id == "alpha")
    assert alpha.spend.total_usd == 30.0  # the misfiled repo contributes nothing to alpha
    assert report.status == "not-ok"


def test_the_committed_over_ceiling_fixture_still_trips_at_all_three_scopes():
    """The gate's provocation fixture is re-checked here, from the committed bytes."""
    from conftest import ROLLUP_DIR

    from inputs import load_inputs
    from model import project

    fixture = ROLLUP_DIR / "fixtures" / "over-ceiling"
    loaded = load_inputs(fixture / "org.yaml", fixture / "inventory", ROLLUP_DIR / "schema.yaml")
    assert loaded.problems == ()
    assert loaded.org is not None
    report = project(loaded.org, loaded.fleets, inputs=loaded.inputs)

    codes = [f.code for f in report.findings]
    assert "SPEND_OVER_CEILING" in codes
    assert "TENANT_OVER_CEILING" in codes
    assert "ENTERPRISE_OVER_CEILING" in codes
    assert "SPEND_AT_CEILING" in codes  # fx/alpha-two#iac-sme sits on its ceiling
    assert report.status == "not-ok"
    assert report.exit_code == 1
    assert report.enterprise.spend.total_usd == 275.0
    assert report.enterprise.spend.excess_usd == 25.0
    # Non-vacuity: the total is the sum of the three repos, not any one of them.
    per_repo = [sum(s.spend_usd for s in fleet.smes) for fleet in report.fleets]
    assert sorted(per_repo) == [45.0, 90.0, 140.0]
    assert report.enterprise.spend.total_usd == sum(per_repo)
    assert report.enterprise.spend.total_usd not in per_repo
