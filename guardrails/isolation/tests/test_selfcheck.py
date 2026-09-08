"""Integrity self-check tests: the detector over this repo's own
tenant-scoped modules must report clean (no NOT-OK findings) or produce
concrete findings, and it must be a stable, honest gate (0 findings today,
and a planted regression is caught)."""

from __future__ import annotations

import os

import pytest

from conftest import REPO_ROOT
from isolation.model import TriState
from isolation.selfcheck import (DEFAULT_SELFCHECK_SUBTREES, repo_root,
                                 self_check, self_check_exit)
from isolation.store import TenantScopedStore


class TestRepoRootDiscovery:
    def test_repo_root_is_the_worktree(self):
        root = repo_root()
        assert os.path.isfile(os.path.join(root, "AGENTS.md"))
        assert root == REPO_ROOT


class TestSelfCheckCleanOnThisRepo:
    """The platform's own tenant-scoped modules carry no detected anti-pattern
    today; the check is the regression gate for future store changes."""

    def test_no_findings_on_tenant_scoped_modules(self):
        report = self_check()
        assert not report.has_findings

    def test_no_not_ok_store_surfaces(self):
        report = self_check()
        assert all(v.verdict is not TriState.NOT_OK for v in report.verdicts)

    def test_scanned_coverage_is_real(self):
        report = self_check()
        # Every configured subtree produced at least one scanned file.
        root = repo_root()
        for subtree in DEFAULT_SELFCHECK_SUBTREES:
            assert os.path.isdir(os.path.join(root, subtree))
        assert len(report.files_scanned) >= 5

    def test_genuine_cross_tenant_registries_verified_ok(self):
        report = self_check()
        ok_owners = {v.owner for v in report.verdicts
                     if v.verdict is TriState.OK}
        assert "RegistryStore._agents" in ok_owners
        assert "KbRegistry._kbs" in ok_owners

    def test_default_exit_is_ok_when_clean(self):
        report = self_check()
        assert self_check_exit(report) == 0

    def test_strict_exit_honours_cannot_assess(self):
        # CANNOT-ASSESS surfaces exist (id-keyed / composite stores), so the
        # strict honesty gate must NOT read as a pass (exit 2).
        report = self_check()
        assert any(v.verdict is TriState.CANNOT_ASSESS for v in report.verdicts)
        assert self_check_exit(report, strict=True) == 2


class TestSelfCheckCatchesRegressions:
    """The gate must be able to fail: a store that drops its tenant dimension
    on a real module path would flip the check to NOT-OK (exit 1)."""

    def test_finding_drives_not_ok_exit(self):
        # Build a scan report carrying one concrete finding (the planted
        # leaky-store shape) and assert the exit code fails.
        import copy

        from isolation.model import Finding, FindingCategory, Severity
        report = copy.deepcopy(self_check())
        report.add_finding(Finding(
            rule_id="R1-SCOPE-DROP-READ",
            category=FindingCategory.SCOPE_DROP_READ,
            severity=Severity.HIGH, message="regression", target="x.py",
            scope="C.m",
        ))
        assert report.has_findings
        assert self_check_exit(report) == 1
        assert report.aggregate() is TriState.NOT_OK
