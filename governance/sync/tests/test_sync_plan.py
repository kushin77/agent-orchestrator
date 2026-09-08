"""Sync plan/apply seam tests (issue #44, acceptance criterion 4).

Dry-run is the default, apply is explicit, and a failed apply action triggers
the rollback path — an action is never reported applied when it failed. The
action vocabulary matches #40 (install/upgrade/rollback/noop).
"""

from __future__ import annotations

import os

import pytest

import synchelpers
from sync_plan import (
    AssetMaterializer,
    ContentUnavailableError,
    PackSyncAdapter,
    ReconcileAction,
    SyncEngine,
    SyncPlanError,
    plan_provenance_sync,
)


class TestPlanProvenanceSync:
    def test_plan_upgrade_noop_install(self, ecosystem):
        _canonical, consumers = ecosystem
        _local_root, manifest = consumers["acme"]
        desired = {
            "core-guardrails": {"version": "2.0.0",
                                "source_path": "shared/core-guardrails.txt",
                                "local_path": "shared/core-guardrails.txt"},
            "design-tokens": {"version": "1.4.0",
                              "local_path": "shared/design-tokens.txt"},
            "new-bundle": {"version": "1.0.0",
                           "source_path": "shared/new-bundle.txt",
                           "local_path": "vendor/new-bundle.txt"},
        }
        actions = plan_provenance_sync(manifest, desired)
        by_asset = {a["asset"]: a for a in actions}
        assert by_asset["core-guardrails"]["op"] == "upgrade"
        assert by_asset["core-guardrails"]["previous"] == "1.0.0"
        assert by_asset["core-guardrails"]["detail"] == "1.0.0 -> 2.0.0"
        assert by_asset["core-guardrails"]["source_path"] == \
            "shared/core-guardrails.txt"
        assert by_asset["design-tokens"]["op"] == "noop"
        assert by_asset["new-bundle"]["op"] == "install"
        assert by_asset["new-bundle"]["local_path"] == "vendor/new-bundle.txt"

    def test_plan_rejects_target_without_version(self, ecosystem):
        _canonical, consumers = ecosystem
        _local_root, manifest = consumers["acme"]
        with pytest.raises(SyncPlanError):
            plan_provenance_sync(manifest, {"core-guardrails": {}})

    def test_unknown_op_refused(self):
        with pytest.raises(SyncPlanError):
            ReconcileAction(op="sidegrade", asset="a", version="1.0.0")


class TestSyncEngine:
    def test_dry_run_is_default_and_has_no_side_effects(self):
        calls = []

        def executor(action):
            calls.append(action)
            return {"ok": True}

        engine = SyncEngine(executor=executor)
        actions = [ReconcileAction(op="install", asset="a", version="1.0.0",
                                   consumer="acme"),
                   ReconcileAction(op="noop", asset="b", version="1.0.0",
                                   consumer="acme")]
        report = engine.run(actions)
        assert report["dry_run"] is True
        statuses = {o["asset"]: o["status"] for o in report["actions"]}
        assert statuses["a"] == "dry-run"
        assert statuses["b"] == "noop"
        assert calls == []  # no executor side effect in dry-run

    def test_apply_executes_only_non_noop(self):
        calls = []

        def executor(action):
            calls.append(action["asset"])
            return {"ok": True}

        engine = SyncEngine(executor=executor)
        actions = [ReconcileAction(op="install", asset="a", version="1.0.0",
                                   consumer="acme"),
                   ReconcileAction(op="noop", asset="b", version="1.0.0",
                                   consumer="acme")]
        report = engine.run(actions, apply=True)
        assert calls == ["a"]
        statuses = {o["asset"]: o["status"] for o in report["actions"]}
        assert statuses["a"] == "applied"
        assert statuses["b"] == "noop"
        assert report["applied"] == 1

    def test_failed_executor_triggers_rollback(self):
        def executor(action):
            raise RuntimeError("apply exploded")

        rollbacks = []

        def rollback(action, error):
            rollbacks.append(action["asset"])
            return {"restored": True, "reason": str(error)}

        engine = SyncEngine(executor=executor, rollback_executor=rollback)
        actions = [ReconcileAction(op="upgrade", asset="boom",
                                   version="2.0.0", consumer="acme")]
        report = engine.run(actions, apply=True)
        outcome = report["actions"][0]
        assert outcome["status"] == "rolled-back"
        assert outcome["rollback"]["restored"] is True
        assert rollbacks == ["boom"]
        assert report["rolled_back"] == 1
        assert report["failed"] == 0

    def test_failure_without_rollback_executor_is_honest_failed(self):
        def executor(action):
            raise RuntimeError("no rollback available")

        engine = SyncEngine(executor=executor)
        actions = [ReconcileAction(op="install", asset="a", version="1.0.0",
                                   consumer="acme")]
        report = engine.run(actions, apply=True)
        outcome = report["actions"][0]
        assert outcome["status"] == "failed"
        assert "no rollback available" in outcome["error"]
        assert report["failed"] == 1

    def test_malformed_plan_action_refused(self):
        engine = SyncEngine(executor=lambda a: {"ok": True})
        with pytest.raises(SyncPlanError):
            engine.run([{"no_op": True}])


class TestAssetMaterializer:
    def test_apply_materializes_and_rollback_restores(self, tmp_path):
        canon = tmp_path / "canonical"
        synchelpers.write(canon, "shared/core-guardrails.txt",
                          "core-guardrails v2.0.0\n")
        local_root = tmp_path / "local"
        synchelpers.write(local_root, "vendor/core-guardrails.txt",
                          "core-guardrails v1.0.0\n")
        matcher = AssetMaterializer(local_root=str(local_root),
                                    canonical_root=str(canon))
        action = ReconcileAction(
            op="upgrade", asset="core-guardrails", version="2.0.0",
            source_path="shared/core-guardrails.txt",
            local_path="vendor/core-guardrails.txt", previous="1.0.0",
            consumer="acme")
        result = matcher.execute(action)
        local_file = local_root / "vendor/core-guardrails.txt"
        assert local_file.read_text(encoding="utf-8") == \
            "core-guardrails v2.0.0\n"
        assert len(result["sha256"]) == 64
        # rollback restores the previous content
        rollback = matcher.rollback(action, "boom")
        assert rollback["restored"] is True
        assert local_file.read_text(encoding="utf-8") == \
            "core-guardrails v1.0.0\n"

    def test_apply_failure_never_leaves_partial_state(self, tmp_path):
        canon = tmp_path / "canonical"
        synchelpers.write(canon, "shared/design-tokens.txt",
                          "design-tokens v9.0.0\n")
        local_root = tmp_path / "local"
        matcher = AssetMaterializer(local_root=str(local_root),
                                    canonical_root=str(canon))
        engine = SyncEngine(executor=matcher.execute,
                            rollback_executor=matcher.rollback)
        # canonical source for this asset is missing -> apply must fail and
        # roll back (the file must never appear).
        actions = [ReconcileAction(
            op="install", asset="missing-bundle", version="1.0.0",
            source_path="shared/never.txt", local_path="vendor/never.txt",
            consumer="acme")]
        report = engine.run(actions, apply=True)
        outcome = report["actions"][0]
        assert outcome["status"] == "rolled-back"
        assert not os.path.exists(
            os.path.join(str(local_root), "vendor", "never.txt"))

    def test_materializer_without_canonical_source_raises(self, tmp_path):
        matcher = AssetMaterializer(local_root=str(tmp_path))
        action = ReconcileAction(op="install", asset="a", version="1.0.0",
                                 source_path="shared/a.txt",
                                 local_path="vendor/a.txt", consumer="acme")
        with pytest.raises(ContentUnavailableError):
            matcher.execute(action)

    def test_path_traversal_refused(self, tmp_path):
        canon = tmp_path / "canonical"
        canon.mkdir()
        matcher = AssetMaterializer(local_root=str(tmp_path),
                                    canonical_root=str(canon))
        action = ReconcileAction(op="install", asset="evil", version="1.0.0",
                                 source_path="../../etc/passwd",
                                 local_path="vendor/evil.txt", consumer="acme")
        with pytest.raises(ContentUnavailableError):
            matcher.execute(action)


class TestPackSyncAdapterSpec:
    def test_desired_from_spec(self):
        spec = {"packs": {"worker-platform": {"version": "1.1.0"},
                          "data-ops": {"version": "1.0.0"}}}
        assert PackSyncAdapter.desired_from_spec(spec) == \
            {"worker-platform": "1.1.0", "data-ops": "1.0.0"}

    def test_desired_from_spec_rejects_missing_version(self):
        with pytest.raises(SyncPlanError):
            PackSyncAdapter.desired_from_spec({"packs": {"x": {}}})
