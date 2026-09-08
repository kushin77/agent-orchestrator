#!/usr/bin/env python3
"""Sync plan/apply seam (issue #44, acceptance criterion 4).

The scheduled reconciler that keeps each consumer/tenant at a desired set of
assets or packs. This tree owns the reconciliation *policy*; the primitives
(install/upgrade/rollback for agent packs) are owned by ``registry/packs``
(issue #40), whose ``Installer.sync_plan`` this engine consumes read-only.

The seam contract:

- **Dry-run is the default** — ``SyncEngine.run(actions)`` with ``apply=False``
  reports the plan and performs no side effects.
- **Explicit apply** — ``apply=True`` executes each action through an injected
  ``executor`` callable; ``noop`` actions are never executed.
- **Rollback on failure** — when an executor action raises, the engine invokes
  the injected ``rollback_executor(action, error)`` and records the action as
  ``rolled-back`` with the reason; a failed action is never reported as
  applied. A run without a rollback executor marks the action ``failed``
  honestly instead.

Action vocabulary is identical to #40's ``SyncPlanAction``: ``op`` is one of
``install | upgrade | rollback | noop`` with ``pack``/``asset``, ``version``,
``tenantId``/``consumer`` and ``detail`` carried through untouched, so an
action list returned by ``Installer.sync_plan`` drives this engine directly
(see ``PackSyncAdapter`` and the integration tests).
"""

from __future__ import annotations

import os

from model import RECONCILE_OPS
from provenance import content_sha256_of


class SyncPlanError(ValueError):
    """A plan action is malformed or the plan is not well-formed."""


class ContentUnavailableError(SyncPlanError):
    """Canonical content for an asset/pack cannot be resolved (apply fails)."""


class ReconcileAction(dict):
    """One declarative reconcile action (mirrors #40 ``SyncPlanAction``).

    Keys are carried verbatim from the producer (typically
    ``Installer.sync_plan``); the engine reads only ``op`` plus whatever the
    executor needs. ``op`` must be in the closed vocabulary
    ``install | upgrade | rollback | noop``.
    """

    def __init__(self, op, **fields):
        if op not in RECONCILE_OPS:
            raise SyncPlanError("unknown reconcile op '%s' (closed vocabulary "
                                "%s)" % (op, sorted(RECONCILE_OPS)))
        super().__init__(op=op, **fields)

    @property
    def op(self):
        return self["op"]


def plan_provenance_sync(current, desired):
    """Plan reconcile actions from a current manifest vs a desired state.

    ``current`` is the consumer's installed ``ProvenanceManifest``; ``desired``
    maps asset_id -> a target record carrying at least ``version`` plus the
    execution fields the materializer needs (``source_path`` for canonical
    content and ``local_path`` for the consumer-side destination). Returns a
    list of ``ReconcileAction`` using the same op vocabulary as #40:

      install   asset not present in the current manifest
      noop      asset present and already at the desired pin/version
      upgrade   asset present but at an older/different pin

    Install/upgrade actions carry the execution fields through so the same
    plan both reports the change and drives ``AssetMaterializer``.
    """
    actions = []
    for asset_id, target in sorted((desired or {}).items()):
        target = target or {}
        version = target.get("version")
        if version is None:
            raise SyncPlanError("desired state for asset '%s' has no version"
                                % asset_id)
        current_entry = current.asset(asset_id)
        exec_fields = {k: v for k, v in target.items()
                       if k in ("source_path", "local_path", "kind", "sha",
                                "content_sha256") and v is not None}
        if current_entry is None:
            actions.append(ReconcileAction(
                op="install", asset=asset_id, version=version,
                consumer=current.consumer_id(),
                detail="not installed", **exec_fields))
        elif (str(current_entry.get("version")) == str(version)
                and (target.get("sha") is None
                     or current_entry.get("sha") == target.get("sha"))):
            actions.append(ReconcileAction(
                op="noop", asset=asset_id, version=version,
                consumer=current.consumer_id(),
                detail="already at desired"))
        else:
            actions.append(ReconcileAction(
                op="upgrade", asset=asset_id, version=version,
                consumer=current.consumer_id(),
                previous=current_entry.get("version"),
                detail="%s -> %s" % (current_entry.get("version"), version),
                **exec_fields))
    return actions


class SyncEngine:
    """Executes a reconcile plan with dry-run default + rollback on failure."""

    def __init__(self, executor=None, rollback_executor=None):
        # executor(action) -> result; raises on failure.
        self.executor = executor
        # rollback_executor(action, error) -> result; best-effort after a
        # failed executor action.
        self.rollback_executor = rollback_executor

    def run(self, actions, apply=False):
        """Execute ``actions``; return a per-action report (never raises)."""
        outcomes = []
        summary = {op: 0 for op in RECONCILE_OPS}
        for action in actions:
            if not isinstance(action, dict) or not action.get("op"):
                raise SyncPlanError("plan contains a malformed action: %r"
                                    % action)
            op = action["op"]
            if op not in RECONCILE_OPS:
                raise SyncPlanError("plan action has unknown op '%s'" % op)
            base = dict(action)
            if op == "noop":
                summary["noop"] += 1
                outcomes.append(dict(base, status="noop"))
                continue
            if not apply:
                summary[op] += 1
                outcomes.append(dict(base, status="dry-run"))
                continue
            summary[op] += 1
            if self.executor is None:
                outcomes.append(dict(
                    base, status="failed",
                    error="no executor injected for apply mode"))
                continue
            try:
                result = self.executor(action)
                outcomes.append(dict(base, status="applied",
                                     result=result))
            except Exception as exc:  # noqa: BLE001 - engine reports all
                outcome = dict(base, status="failed", error=str(exc))
                if self.rollback_executor is not None:
                    try:
                        rollback = self.rollback_executor(action, exc)
                        outcome["status"] = "rolled-back"
                        outcome["rollback"] = rollback
                    except Exception as rb_exc:  # noqa: BLE001
                        outcome["rollback_error"] = str(rb_exc)
                outcomes.append(outcome)
        return {
            "dry_run": not apply,
            "actions": outcomes,
            "summary": summary,
            "applied": sum(1 for o in outcomes
                           if o.get("status") == "applied"),
            "rolled_back": sum(1 for o in outcomes
                               if o.get("status") == "rolled-back"),
            "failed": sum(1 for o in outcomes
                          if o.get("status") == "failed"),
        }


class AssetMaterializer:
    """Offline apply/rollback executor for provenance assets.

    ``install``/``upgrade`` copy the canonical content (from ``canonical_root``
    at ``source_path``, or a caller-supplied ``content_provider``) into the
    consumer's ``local_path`` atomically, keeping a backup of the previous
    local content so ``rollback`` can restore it. When canonical content is
    unavailable the materializer raises ``ContentUnavailableError`` — the
    engine then runs the rollback path (never reports the action applied).
    """

    def __init__(self, local_root, canonical_root=None, content_provider=None):
        self.local_root = os.path.abspath(local_root)
        self.canonical_root = (os.path.abspath(canonical_root)
                               if canonical_root else None)
        self.content_provider = content_provider
        # local_abs -> previous bytes, or None when the asset was absent
        # before the apply (rollback then removes the file).
        self._backups = {}

    # -- canonical content ---------------------------------------------------
    def _canonical_bytes(self, action):
        provider = self.content_provider
        if provider is not None:
            return provider(action)
        source_path = action.get("source_path")
        if not source_path:
            raise ContentUnavailableError(
                "cannot materialize %s: action carries no source_path for "
                "canonical content" % (action.get("asset")
                                       or action.get("pack")))
        if not self.canonical_root:
            raise ContentUnavailableError(
                "cannot materialize %s: no canonical_root or content_provider"
                % (action.get("asset") or action.get("pack")))
        canonical_file = os.path.abspath(
            os.path.join(self.canonical_root, source_path))
        if os.path.commonpath([self.canonical_root, canonical_file]) != \
                self.canonical_root:
            raise ContentUnavailableError(
                "source_path escapes canonical_root: %s" % source_path)
        if not os.path.isfile(canonical_file):
            raise ContentUnavailableError(
                "canonical content unavailable: %s (source deleted or never "
                "mirrored)" % source_path)
        with open(canonical_file, "rb") as fh:
            return fh.read()

    # -- executor ------------------------------------------------------------
    def execute(self, action):
        """Apply one install/upgrade action (used as the engine executor)."""
        op = action["op"]
        if op not in ("install", "upgrade"):
            raise SyncPlanError("materializer does not execute op '%s'" % op)
        local_rel = action.get("local_path")
        if not local_rel:
            raise SyncPlanError(
                "%s action for %s has no local_path"
                % (op, action.get("asset") or action.get("pack")))
        local_file = os.path.abspath(os.path.join(self.local_root, local_rel))
        if os.path.commonpath([self.local_root, local_file]) != \
                self.local_root:
            raise SyncPlanError("local_path escapes local_root: %s"
                                % local_rel)
        content = self._canonical_bytes(action)
        previous = None
        if os.path.isfile(local_file):
            with open(local_file, "rb") as fh:
                previous = fh.read()
        os.makedirs(os.path.dirname(local_file), exist_ok=True)
        tmp = local_file + ".sync.tmp"
        with open(tmp, "wb") as fh:
            fh.write(content)
        os.replace(tmp, local_file)
        self._backups[local_file] = previous
        return {"materialized": local_file,
                "sha256": content_sha256_of(local_file),
                "previous_version": action.get("previous")}

    def rollback(self, action, error=None):
        """Best-effort restore after a failed apply (engine rollback_executor)."""
        local_rel = action.get("local_path")
        if not local_rel:
            return {"restored": False, "reason": "no local_path recorded"}
        local_file = os.path.abspath(os.path.join(self.local_root, local_rel))
        if local_file not in self._backups:
            return {"restored": False,
                    "reason": "no prior state recorded for %s" % local_rel}
        previous = self._backups.pop(local_file)
        if previous is None:
            # Asset was not present before the failed apply: remove it.
            if os.path.exists(local_file):
                os.remove(local_file)
        else:
            os.makedirs(os.path.dirname(local_file), exist_ok=True)
            with open(local_file, "wb") as fh:
                fh.write(previous)
        return {"restored": True, "to": local_rel,
                "reason": str(error) if error else "manual"}


class PackSyncAdapter:
    """Read-only adapter over the #40 ``registry.packs.Installer`` seam.

    Produces the desired pack map from a sync target spec and consumes the
    action list ``Installer.sync_plan`` returns, so the reconcile engine
    drives the real #40 primitive ops. This module never edits
    ``registry/packs`` — it imports and calls it only (the integration test
    proves the wiring against the real ``Installer.sync_plan``).
    """

    @staticmethod
    def desired_from_spec(spec):
        """``{pack_id: version}`` from an ``ao.sync/sync-target-v1`` spec."""
        desired = {}
        for pack_id, target in sorted((spec.get("packs") or {}).items()):
            version = (target or {}).get("version")
            if version is None:
                raise SyncPlanError(
                    "sync target for pack '%s' has no version" % pack_id)
            desired[pack_id] = version
        return desired

    @staticmethod
    def actions_for(installer, tenant_id, desired):
        """Real #40 ``Installer.sync_plan`` -> list of ReconcileAction."""
        actions = installer.sync_plan(tenant_id, desired)
        return [ReconcileAction(op=a.get("op"), **{
            k: v for k, v in a.items() if k != "op"}) for a in actions]
