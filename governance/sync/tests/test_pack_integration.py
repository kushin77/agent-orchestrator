"""Integration wiring to the #40 ``registry/packs`` sync_plan seam (issue #44).

The sync engine consumes the real ``Installer.sync_plan`` action list
(read-only import of ``registry/packs`` — this lane never edits it). The real
#40 method computes the plan from a tenant's recorded install history; this
suite proves the governance/sync engine drives that exact output shape
(``op``/``pack``/``version``/``tenantId``/``detail``) with dry-run default,
explicit apply and rollback on failure.
"""

from __future__ import annotations

import os
import sys

# registry/packs is importable when ``registry/`` is on sys.path (the package
# re-exports at registry/packs/__init__.py document this contract).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_REGISTRY_DIR = os.path.join(_REPO_ROOT, "registry")
if _REGISTRY_DIR not in sys.path:
    sys.path.insert(0, _REGISTRY_DIR)

from packs.installer import Installer, SyncPlanAction  # noqa: E402
from packs.pack_events import PackEventLog  # noqa: E402
from packs.registry import PackRegistry  # noqa: E402
from sync_plan import (  # noqa: E402
    PackSyncAdapter,
    ReconcileAction,
    SyncEngine,
)


def _make_installer(tmp_path):
    """Real #40 Installer over a real PackRegistry (no content installs)."""
    registry = PackRegistry(event_log=PackEventLog())
    # Simulate tenants' recorded install history (the registry ledger shape
    # ``Installer.sync_plan`` reads: active version per (tenant, pack)).
    registry._installs["acme"] = {"worker-platform": ["1.0.0"]}
    registry._installs["globex"] = {"worker-platform": ["1.0.0", "1.1.0"]}
    return Installer(registry, str(tmp_path / "content"), "unused-public-key")


class TestRealSyncPlanSeam:
    def test_installer_sync_plan_returns_action_list(self, tmp_path):
        installer = _make_installer(tmp_path)
        plan = installer.sync_plan("acme",
                                   {"worker-platform": "1.0.0",
                                    "data-ops": "1.0.0"})
        assert all(isinstance(a, SyncPlanAction) for a in plan)
        by_pack = {a["pack"]: a for a in plan}
        # worker-platform is already at the desired version -> noop
        assert by_pack["worker-platform"]["op"] == "noop"
        # data-ops was never installed -> install
        assert by_pack["data-ops"]["op"] == "install"
        for action in plan:
            assert action["op"] in ("install", "upgrade", "rollback", "noop")
            assert action["tenantId"] == "acme"
            assert action["version"]

    def test_sync_plan_upgrade_when_behind(self, tmp_path):
        installer = _make_installer(tmp_path)
        plan = installer.sync_plan("globex", {"worker-platform": "1.2.0"})
        by_pack = {a["pack"]: a for a in plan}
        # globex is at 1.1.0, desired 1.2.0 -> upgrade
        assert by_pack["worker-platform"]["op"] == "upgrade"
        assert by_pack["worker-platform"]["detail"] == "1.1.0 -> 1.2.0"


class TestEngineDrivesRealPlan:
    def test_engine_consumes_sync_plan_actions_dry_run(self, tmp_path):
        installer = _make_installer(tmp_path)
        actions = PackSyncAdapter.actions_for(
            installer, "acme",
            {"worker-platform": "1.0.0", "data-ops": "1.0.0"})
        assert all(isinstance(a, ReconcileAction) for a in actions)
        calls = []

        def executor(action):
            calls.append(action["op"])
            return {"ok": True}

        report = SyncEngine(executor=executor).run(actions)
        statuses = {o["pack"]: o["status"] for o in report["actions"]}
        assert statuses["worker-platform"] == "noop"
        assert statuses["data-ops"] == "dry-run"
        assert calls == []  # dry-run performed no side effects

    def test_engine_applies_install_and_skips_noop(self, tmp_path):
        installer = _make_installer(tmp_path)
        actions = PackSyncAdapter.actions_for(
            installer, "acme",
            {"worker-platform": "1.0.0", "data-ops": "1.0.0"})
        calls = []

        def executor(action):
            calls.append((action["op"], action["pack"], action["version"]))
            return {"ok": True}

        report = SyncEngine(executor=executor).run(actions, apply=True)
        assert ("install", "data-ops", "1.0.0") in calls
        assert ("noop", "worker-platform", "1.0.0") not in calls
        statuses = {o["pack"]: o["status"] for o in report["actions"]}
        assert statuses["data-ops"] == "applied"
        assert statuses["worker-platform"] == "noop"
        assert report["applied"] == 1

    def test_engine_rolls_back_a_failed_pack_install(self, tmp_path):
        installer = _make_installer(tmp_path)
        actions = PackSyncAdapter.actions_for(
            installer, "acme",
            {"worker-platform": "1.0.0", "data-ops": "1.0.0"})
        rollbacks = []

        def executor(action):
            if action["op"] == "install":
                raise RuntimeError("pack install refused at apply time")
            return {"ok": True}

        def rollback(action, error):
            rollbacks.append(action["pack"])
            return {"restored": True, "reason": str(error)}

        report = SyncEngine(executor=executor,
                            rollback_executor=rollback).run(actions,
                                                            apply=True)
        outcome = next(o for o in report["actions"]
                       if o["pack"] == "data-ops")
        assert outcome["status"] == "rolled-back"
        assert outcome["rollback"]["restored"] is True
        assert rollbacks == ["data-ops"]
        assert report["rolled_back"] == 1

    def test_rollback_action_maps_to_installer_rollback_seam(self, tmp_path):
        """op=rollback is part of the #40 vocabulary the engine can drive."""
        installer = _make_installer(tmp_path)
        # An explicit rollback action (the #40 direct rollback seam).
        action = ReconcileAction(op="rollback", pack="worker-platform",
                                 version="1.0.0", tenantId="acme",
                                 detail="manual revert")
        calls = []

        def executor(a):
            calls.append("executor:" + a["op"])
            return {"ok": True}

        report = SyncEngine(executor=executor).run([action], apply=True)
        assert report["actions"][0]["status"] == "applied"
        assert calls == ["executor:rollback"]
        # The real Installer exposes rollback() as the seam an executor binds.
        assert callable(installer.rollback)
