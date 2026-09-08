#!/usr/bin/env python3
"""Tenant install/upgrade path for agent packs (issue #40).

Consumer-trust + drift-resilient install:

1. **Signature gate** — the pack's attestation signature MUST verify against
   the publisher public key. A pack with a missing or bad signature FAILS
   install (negative-tested; fail closed even when ``cryptography`` is
   unavailable).
2. **Lifecycle gate** — only ``live`` packs are installable (registry state).
3. **Content materialize + verify** — each contents-manifest artifact is
   decoded (embedded base64 ``data``, or fetched through an injected content
   provider) and written under the tenant content root; every written file is
   re-hashed against the manifest's pinned sha256. A missing file or a sha256
   mismatch is content drift and fails the install.
4. **Post-install drift detection** — ``verify_installed`` re-hashes the
   on-disk tree against the installed manifest at any later time.
5. **Upgrade with rollback** — ``upgrade`` installs a new version; on ANY
   failure (signature, drift, exception) it rolls back to the previous
   version: the prior tree is restored/kept active, an append-only
   ``rollback`` event is recorded, and ``UpgradeRollbackError`` (with the
   reason) is raised. ``rollback`` is also exposed directly as the seam the
   phase-8 sync engine (issue #44) will drive.

**#44 sync-engine seam**: the sync engine is the scheduled reconciler that
keeps each tenant at a desired pack set (phase 8, issue #44). This tree owns
the install/upgrade/drift/rollback primitives; ``Installer.sync_plan`` returns
the declarative action list (install / upgrade / rollback / noop) the #44
engine will consume on each reconcile tick. The engine itself is out of scope
here.

Tenant layout under the content root:

    <root>/<pack_id>/<version>/<artifact_type>/<ref-slug>
    <root>/<pack_id>/active.<tenant_id>      # pointer: current active version
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re

from packs import attestation
from packs.pack_events import PackEventLog
from packs.registry import PackRegistry, PackRegistryError

SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class PackInstallError(PackRegistryError):
    """Install refused (unknown pack, non-live, content, ...)."""


class PackSignatureError(PackInstallError):
    """Signature missing or does not verify (consumer trust, fail closed)."""


class ContentDriftError(PackInstallError):
    """Installed content is missing or its sha256 does not match the manifest."""


class UpgradeRollbackError(PackInstallError):
    """An upgrade failed and the previous version was rolled back."""

    def __init__(self, message, pack_id, previous_version, reason):
        super().__init__(message)
        self.pack_id = pack_id
        self.previous_version = previous_version
        self.reason = reason


class SyncPlanAction(dict):
    """One declarative reconcile action for the #44 sync engine.

    Keys: op (install|upgrade|rollback|noop), pack, version (target),
    tenantId, detail.
    """


def _slug(ref):
    return SLUG_RE.sub("-", ref)


def _sha256_file(path, chunk=65536):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class Installer:
    """Offline tenant install/upgrade engine for agent packs."""

    def __init__(self, registry, content_root, public_key_pem,
                 event_log=None, content_provider=None):
        self._registry = registry
        self._root = os.path.abspath(content_root)
        self._public_key = public_key_pem
        self._log = event_log if event_log is not None else registry.event_log
        # content_provider(type, ref) -> bytes; None = decode embedded data
        self._provider = content_provider

    # -- helpers -------------------------------------------------------------
    def _emit(self, event, status, pack, version=None, tenant_id=None,
              actor=None, detail=None):
        return self._log.append(event, status, pack, version=version,
                                tenant_id=tenant_id, actor=actor,
                                detail=detail)

    def _artifact_bytes(self, entry, type_):
        if self._provider is not None:
            return self._provider(type_, entry["ref"])
        try:
            return base64.b64decode(entry["data"])
        except Exception as exc:  # pragma: no cover - defensive
            raise PackInstallError(
                "artifact %s/%s has malformed base64 data: %s"
                % (type_, entry["ref"], exc))

    def _verify_signature(self, doc):
        """Consumer-trust gate: missing/bad signature fails install."""
        att = doc.get("attestation")
        if not isinstance(att, dict) or not att.get("signature"):
            raise PackSignatureError(
                "install refused: pack %s %s has no attestation signature"
                % (doc.get("id"), doc.get("version")))
        if not attestation.HAS_CRYPTO:
            raise PackSignatureError(
                "install refused: cryptography unavailable; signature cannot "
                "be verified (fail closed)")
        if not attestation.verify_pack(doc, self._public_key):
            raise PackSignatureError(
                "install refused: pack %s %s signature does not verify "
                "(tampered or wrong key)" % (doc.get("id"),
                                             doc.get("version")))

    def _materialize(self, doc, tenant_id):
        """Write every contents artifact under the tenant content root.

        Returns (version_dir, written_paths). Raises ContentDriftError when an
        artifact's decoded bytes do not match the manifest sha256.
        """
        pid = doc["id"]
        version = doc["version"]
        base = os.path.join(self._root, pid, version)
        written = []
        for type_, entries in (doc.get("contents") or {}).items():
            for entry in entries:
                data = self._artifact_bytes(entry, type_)
                declared = (entry.get("sha256") or "").lower()
                if not HEX_RE.match(declared):
                    raise ContentDriftError(
                        "artifact %s/%s sha256 '%s' is malformed"
                        % (type_, entry.get("ref"), declared))
                actual = hashlib.sha256(data).hexdigest()
                if actual != declared:
                    raise ContentDriftError(
                        "artifact %s/%s content hash mismatch (declared %s, "
                        "actual %s)" % (type_, entry.get("ref"), declared,
                                        actual))
                rel = os.path.join(type_, _slug(entry.get("ref", "")))
                dest = os.path.join(base, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(data)
                written.append(dest)
        if not written:
            raise PackInstallError(
                "pack %s %s has an empty contents manifest" % (pid, version))
        return base, written

    def _verify_tree(self, doc, base):
        """Re-hash the materialized tree against the manifest (drift detect)."""
        missing = []
        mismatched = []
        for type_, entries in (doc.get("contents") or {}).items():
            for entry in entries:
                rel = os.path.join(type_, _slug(entry.get("ref", "")))
                dest = os.path.join(base, rel)
                if not os.path.exists(dest):
                    missing.append(rel)
                    continue
                if _sha256_file(dest) != (entry.get("sha256") or "").lower():
                    mismatched.append(rel)
        if missing or mismatched:
            raise ContentDriftError(
                "installed content drift for %s %s: missing=%s mismatch=%s"
                % (doc.get("id"), doc.get("version"), missing, mismatched))

    # -- install / verify / upgrade / rollback --------------------------------
    def install(self, tenant_id, pack_id, version, actor=None):
        """Install ``pack_id@version`` for ``tenant_id``.

        Full gate chain: signature (consumer trust) -> live lifecycle ->
        materialize -> content verify. On success the install is recorded in
        the registry consumption ledger and an install event is appended.
        """
        try:
            doc = self._registry.get(pack_id, version)
        except PackRegistryError as exc:
            raise PackInstallError(str(exc))
        self._verify_signature(doc)
        if not self._registry.is_installable(pack_id):
            raise PackInstallError(
                "install refused: pack '%s' is '%s' (only live packs are "
                "installable)" % (pack_id,
                                  self._registry.state(pack_id)))
        base, _written = self._materialize(doc, tenant_id)
        self._verify_tree(doc, base)
        # pointer: active version for this tenant
        active_dir = os.path.join(self._root, pack_id)
        os.makedirs(active_dir, exist_ok=True)
        with open(os.path.join(active_dir, "active.%s" % _slug(tenant_id)),
                  "w", encoding="utf-8") as fh:
            fh.write(version + "\n")
        self._registry.record_install(tenant_id, pack_id, version, actor=actor)
        return {"pack": pack_id, "version": version, "tenantId": tenant_id,
                "root": base}

    def verify_installed(self, tenant_id, pack_id):
        """Post-install drift detection: re-hash on-disk tree vs manifest."""
        version = self._registry.active_version(tenant_id, pack_id)
        if version is None:
            raise PackInstallError(
                "tenant '%s' has no installed version of pack '%s'"
                % (tenant_id, pack_id))
        doc = self._registry.get(pack_id, version)
        base = os.path.join(self._root, pack_id, version)
        if not os.path.isdir(base):
            raise ContentDriftError(
                "installed tree for %s %s is missing (tenant %s)"
                % (pack_id, version, tenant_id))
        self._verify_tree(doc, base)
        return {"pack": pack_id, "version": version, "tenantId": tenant_id,
                "ok": True}

    def _restore(self, tenant_id, pack_id, version):
        """Ensure the previous version's tree is present (rollback restore)."""
        doc = self._registry.get(pack_id, version)
        base, _written = self._materialize(doc, tenant_id)
        self._verify_tree(doc, base)
        return base

    def upgrade(self, tenant_id, pack_id, new_version, actor=None):
        """Upgrade a tenant to ``new_version``; roll back on ANY failure.

        Returns the install result on success. On failure the previous active
        version is restored and stays active, a ``rollback`` event is
        appended, and ``UpgradeRollbackError`` (with reason) is raised —
        negative-tested.
        """
        previous = self._registry.active_version(tenant_id, pack_id)
        if previous is None:
            raise PackInstallError(
                "upgrade refused: tenant '%s' has no installed version of "
                "pack '%s' to upgrade" % (tenant_id, pack_id))
        if previous == new_version:
            raise PackInstallError(
                "upgrade refused: tenant '%s' already has pack %s at the "
                "target version %s" % (tenant_id, pack_id, new_version))
        try:
            result = self.install(tenant_id, pack_id, new_version, actor=actor)
            self._emit("upgrade", "upgraded", pack_id, version=new_version,
                       tenant_id=tenant_id, actor=actor,
                       detail={"from": previous, "to": new_version})
            return result
        except PackSignatureError as exc:
            self._do_rollback(tenant_id, pack_id, previous, str(exc), actor)
            raise UpgradeRollbackError(
                "upgrade of %s to %s failed (signature): %s; rolled back to "
                "%s" % (pack_id, new_version, exc, previous), pack_id,
                previous, "signature") from exc
        except ContentDriftError as exc:
            self._do_rollback(tenant_id, pack_id, previous, str(exc), actor)
            raise UpgradeRollbackError(
                "upgrade of %s to %s failed (content drift): %s; rolled back "
                "to %s" % (pack_id, new_version, exc, previous), pack_id,
                previous, "content-drift") from exc
        except PackRegistryError as exc:
            self._do_rollback(tenant_id, pack_id, previous, str(exc), actor)
            raise UpgradeRollbackError(
                "upgrade of %s to %s failed: %s; rolled back to %s"
                % (pack_id, new_version, exc, previous), pack_id, previous,
                "registry") from exc
        except OSError as exc:  # pragma: no cover - defensive
            self._do_rollback(tenant_id, pack_id, previous, str(exc), actor)
            raise UpgradeRollbackError(
                "upgrade of %s to %s failed (io): %s; rolled back to %s"
                % (pack_id, new_version, exc, previous), pack_id, previous,
                "io") from exc

    def _do_rollback(self, tenant_id, pack_id, previous, reason, actor=None):
        """Restore ``previous`` as the active version + append rollback event."""
        try:
            self._restore(tenant_id, pack_id, previous)
        except PackRegistryError:
            pass  # previous tree still exists from before; best-effort restore
        # fix the registry ledger: drop the failed version, keep previous
        history = self._installs_history(tenant_id, pack_id)
        if history and history[-1] != previous:
            history.pop()
        self._pointer_write(tenant_id, pack_id, previous)
        self._emit("rollback", "rolled_back", pack_id, version=previous,
                   tenant_id=tenant_id, actor=actor,
                   detail={"to": previous, "reason": reason})

    def rollback(self, tenant_id, pack_id, reason="manual", actor=None):
        """Direct rollback seam (#44 sync engine): revert to previous version."""
        previous = self._registry.previous_version(tenant_id, pack_id)
        current = self._registry.active_version(tenant_id, pack_id)
        if previous is None:
            raise PackInstallError(
                "rollback refused: tenant '%s' has no previous version of "
                "pack '%s' to roll back to" % (tenant_id, pack_id))
        self._restore(tenant_id, pack_id, previous)
        self._do_rollback(tenant_id, pack_id, previous, reason, actor=actor)
        return {"pack": pack_id, "from": current, "to": previous,
                "tenantId": tenant_id}

    # -- registry ledger helpers ----------------------------------------------
    def _installs_history(self, tenant_id, pack_id):
        return self._registry._installs.setdefault(tenant_id, {}).setdefault(
            pack_id, [])

    def _pointer_write(self, tenant_id, pack_id, version):
        active_dir = os.path.join(self._root, pack_id)
        os.makedirs(active_dir, exist_ok=True)
        with open(os.path.join(active_dir, "active.%s" % _slug(tenant_id)),
                  "w", encoding="utf-8") as fh:
            fh.write(version + "\n")

    # -- #44 sync-engine seam --------------------------------------------------
    def sync_plan(self, tenant_id, desired):
        """Declarative reconcile plan for the phase-8 sync engine (#44).

        ``desired`` = {pack_id: version} the tenant should be at. Returns a
        list of ``SyncPlanAction`` the #44 engine will drive each reconcile
        tick. This tree owns the primitive ops; the engine (issue #44) owns
        scheduling/reconciliation.
        """
        plan = []
        for pack_id, version in sorted(desired.items()):
            current = self._registry.active_version(tenant_id, pack_id)
            if current is None:
                plan.append(SyncPlanAction(op="install", pack=pack_id,
                                           version=version,
                                           tenantId=tenant_id,
                                           detail="not installed"))
            elif current == version:
                plan.append(SyncPlanAction(op="noop", pack=pack_id,
                                           version=version,
                                           tenantId=tenant_id,
                                           detail="already at desired"))
            else:
                plan.append(SyncPlanAction(op="upgrade", pack=pack_id,
                                           version=version,
                                           tenantId=tenant_id,
                                           detail="%s -> %s"
                                                  % (current, version)))
        return plan
