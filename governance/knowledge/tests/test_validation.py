"""Validation and drift — the negative controls behind the gate (issue #139).

Each test breaks the tree in one specific way and asserts the *named* finding
appears. A validation path that cannot produce its finding is decoration, so these
are the tests that make the gate's failure modes real.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    "governance_knowledge_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
SNAPSHOT = _conftest.SNAPSHOT
sample_path = _conftest.sample_path

from indexer import build_index, drift_findings, load_catalog, write_catalog
from model import (
    CODE_EXPECTED_KIND_UNAVAILABLE,
    CODE_INTEGRITY_DRIFT,
    CODE_MALFORMED_ISSUE_SNAPSHOT,
    CODE_REQUIRED_KIND_EMPTY,
    CODE_REQUIRED_SOURCE_MISSING,
    CODE_SECRET_POLICY_VIOLATION,
    KIND_ADR,
    errors,
)
from sources import required_specs


def codes(findings):
    return sorted(finding.code for finding in findings)


# -- structural negative controls -------------------------------------------


def test_missing_required_source_is_an_error(tree: Path):
    """NEGATIVE CONTROL: delete a required asset -> named error."""
    (tree / "AGENTS.md").unlink()

    index = build_index(tree, owner_repo="acme/widgets")

    assert CODE_REQUIRED_SOURCE_MISSING in codes(index.findings)
    finding = next(
        f for f in index.findings if f.code == CODE_REQUIRED_SOURCE_MISSING
    )
    assert "AGENTS.md" in finding.message
    assert errors(index.findings)


def test_emptying_a_required_kind_is_an_error(tree: Path):
    """NEGATIVE CONTROL: a required kind with zero items fails, not passes.

    Every source of the kind must go: while one ADR source still resolves the kind
    is covered, and only the missing glob is reported.
    """
    for spec in [s for s in required_specs() if s.kind == KIND_ADR]:
        (tree / sample_path(spec.pattern)).unlink()

    index = build_index(tree, owner_repo="acme/widgets")

    assert CODE_REQUIRED_KIND_EMPTY in codes(index.findings)
    entry = next(c for c in index.coverage if c.kind == KIND_ADR)
    assert entry.status == "absent" and entry.count == 0


def test_malformed_issue_snapshot_is_an_error(tree: Path):
    """NEGATIVE CONTROL: unparseable board snapshot -> named error."""
    (tree / ".board" / "snapshot.json").write_text("{ not json", encoding="utf-8")

    index = build_index(tree, owner_repo="acme/widgets")

    assert CODE_MALFORMED_ISSUE_SNAPSHOT in codes(index.findings)


def test_snapshot_without_issues_list_is_an_error(tree: Path):
    (tree / ".board" / "snapshot.json").write_text(
        json.dumps({"generated_at": "x", "source": "y"}), encoding="utf-8"
    )

    index = build_index(tree, owner_repo="acme/widgets")

    assert CODE_MALFORMED_ISSUE_SNAPSHOT in codes(index.findings)


def test_snapshot_items_missing_numbers_are_skipped_not_fatal(tree: Path):
    payload = dict(SNAPSHOT)
    payload["issues"] = [{"title": "no number"}, SNAPSHOT["issues"][0]]
    (tree / ".board" / "snapshot.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    index = build_index(tree, owner_repo="acme/widgets")

    assert errors(index.findings) == ()
    assert [item.id for item in index.items if item.kind == "issue-metadata"] == [
        "issue-139"
    ]


def test_expected_kind_warning_names_a_reason(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets")
    warning = next(
        f for f in index.findings if f.code == CODE_EXPECTED_KIND_UNAVAILABLE
    )
    assert "unreachable" in warning.message
    assert "vendor/CMR" in warning.message


# -- secret policy -----------------------------------------------------------


def test_secret_shaped_asset_is_an_error_not_a_silent_index(tree: Path):
    """NEGATIVE CONTROL: an indexed asset carrying a credential fails the build."""
    credentialed_literal = _conftest.credentialed_literal

    def reader(path):
        return credentialed_literal()

    index = build_index(tree, owner_repo="acme/widgets", secret_reader=reader)

    assert CODE_SECRET_POLICY_VIOLATION in codes(index.findings)
    # The finding must not echo the credential back out.
    finding = next(
        f for f in index.findings if f.code == CODE_SECRET_POLICY_VIOLATION
    )
    assert credentialed_literal() not in finding.message
    assert "redacted" in finding.message


# -- drift -------------------------------------------------------------------


def test_unchanged_tree_reports_no_drift(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")
    write_catalog(index, tree / "catalog.json")
    baseline = load_catalog(tree / "catalog.json")

    assert drift_findings(index, baseline) == []


def test_changed_asset_reports_drift(tree: Path):
    """NEGATIVE CONTROL: an edited asset shows up as drift, named."""
    index = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")
    write_catalog(index, tree / "catalog.json")
    baseline = load_catalog(tree / "catalog.json")

    (tree / "AGENTS.md").write_text("# Changed\n\nnew content\n", encoding="utf-8")
    rebuilt = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")

    drift = drift_findings(rebuilt, baseline)
    assert drift and all(f.code == CODE_INTEGRITY_DRIFT for f in drift)
    assert any(f.path == "AGENTS.md" for f in drift)


def test_new_and_removed_assets_are_reported(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")
    write_catalog(index, tree / "catalog.json")
    baseline = load_catalog(tree / "catalog.json")

    (tree / "CHANGELOG.md").unlink()  # removes a governance item
    rebuilt = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")

    messages = [f.message for f in drift_findings(rebuilt, baseline)]
    assert any("no longer indexed" in message for message in messages)


def test_drift_is_a_warning_so_the_gate_can_still_pass(tree: Path):
    """Drift informs a refresh; it must not wedge every doc edit."""
    index = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")
    write_catalog(index, tree / "catalog.json")
    baseline = load_catalog(tree / "catalog.json")

    (tree / "AGENTS.md").write_text("# Changed\n", encoding="utf-8")
    rebuilt = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")

    drift = drift_findings(rebuilt, baseline)
    assert drift
    assert all(finding.severity == "warning" for finding in drift)
    assert errors(rebuilt.findings) == ()
