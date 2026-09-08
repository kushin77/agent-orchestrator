#!/usr/bin/env python3
"""AgentPack registry + catalog (issue #40) — the pack control surface.

``PackRegistry`` is the "module registry for agent capabilities": it holds the
published pack versions, enforces the pack lifecycle
(``planned -> live -> paused -> retired``), validates every publish against
the AgentPack schema (an invalid pack FAILS publish — no-false-green), and
provides the catalog surface (search, crossref) plus tenant consumption
tracking (who installed which pack/version). Every mutating call appends to
the append-only, hash-chained pack event log (``packs/pack_events.py``).

Lifecycle state is per pack id and held by the registry (the immutable,
attestation-signed release snapshot records the state at publish time; the
registry drives transitions from there and never rewrites a release file).
Only ``live`` packs are installable — the installer
(``packs/installer.py``) consults ``PackRegistry.state``.

Closed catalog vocabulary (categories/artifact types) and the AgentPack schema
are loaded from ``pack-catalog.yaml`` / ``agent-pack.schema.json``.

Importable when ``registry/`` is on sys.path:

    import sys; sys.path.insert(0, "registry")
    from packs import registry as packreg
    from packs.pack_events import open_pack_event_log

    log = open_pack_event_log()
    reg = packreg.PackRegistry(event_log=log)
    reg.publish(pack_doc)                  # schema-validated; invalid fails
    reg.pause("worker-platform")
    reg.record_install("acme", "worker-platform", "1.0.0")
    reg.search(category="coding")
    reg.crossref("worker-platform")
"""

from __future__ import annotations

import re

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover - guarded below
    HAS_JSONSCHEMA = False

from packs.pack_events import PackEventLog

ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

# Lifecycle transition table: state -> set of legal next states.
TRANSITIONS = {
    "planned": {"live"},   # publish
    "live": {"paused", "retired"},
    "paused": {"live", "retired"},   # resume / retire
    "retired": set(),      # terminal
}


class PackRegistryError(Exception):
    """Base registry error."""


class PackPublishError(PackRegistryError):
    """Publish refused: the pack is schema-invalid or not publishable."""


class PackLifecycleError(PackRegistryError):
    """Lifecycle transition refused (illegal or terminal state)."""


class PackNotFoundError(PackRegistryError):
    """No such pack (id / version)."""


class PackAlreadyExistsError(PackRegistryError):
    """A pack version is already published (immutable)."""


def _schema_valid_errors(data, schema, label):
    """JSON Schema conformance; fails closed when jsonschema is missing."""
    if not HAS_JSONSCHEMA:
        return ["%s: jsonschema unavailable; schema conformance cannot run "
                "(fail closed)" % label]
    errors = []
    try:
        validator = jsonschema.Draft7Validator(schema)
        for err in validator.iter_errors(data):
            path = "/".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append("%s: schema %s: %s" % (label, path, err.message))
    except Exception as exc:  # pragma: no cover - defensive
        errors.append("%s: schema validation crashed: %s" % (label, exc))
    return errors


class PackRegistry:
    """Publish registry + catalog + consumption tracking for agent packs."""

    def __init__(self, event_log=None, schema=None, catalog=None):
        self._log = event_log if event_log is not None else PackEventLog()
        # published pack versions: {pack_id: {version: doc}}
        self._packs = {}
        # current lifecycle per pack id: {pack_id: lifecycle}
        self._state = {}
        # tenant install ledger: {tenant_id: {pack_id: [version, ...]}}
        # (ordered: oldest -> newest; last is the active version)
        self._installs = {}
        # catalog search index over published docs (id -> doc) for live packs
        self._schema = schema
        self._catalog = catalog

    # -- lifecycle events ---------------------------------------------------
    def _emit(self, event, status, pack, version=None, tenant_id=None,
              actor=None, detail=None):
        return self._log.append(event, status, pack, version=version,
                                tenant_id=tenant_id, actor=actor, detail=detail)

    # -- publish path --------------------------------------------------------
    def publish(self, pack, schema=None, actor=None):
        """Schema-validate + publish one AgentPack document.

        Fails closed: an invalid pack (schema conformance or structural
        membership) raises ``PackPublishError`` and is NEVER published
        (negative-tested). A published (id, version) is immutable — a second
        publish of the same (id, version) raises ``PackAlreadyExistsError``.
        The pack must carry an attestation block (a publisher always ships a
        signature; consumer trust is enforced at install time).
        """
        schema = schema or self._schema
        if not isinstance(pack, dict):
            raise PackPublishError("publish: pack must be a YAML/JSON object")
        errors = _schema_valid_errors(pack, schema,
                                      "publish/%s" % pack.get("id", "?"))
        pid = pack.get("id")
        version = pack.get("version")
        if not isinstance(pid, str) or not ID_RE.match(pid or ""):
            errors.append("publish: id '%s' must match ^[a-z][a-z0-9-]*$" % pid)
        if not isinstance(version, str) or not VERSION_RE.match(version or ""):
            errors.append("publish: version '%s' must be semantic X.Y.Z"
                          % version)
        att = pack.get("attestation")
        if not isinstance(att, dict) or not att.get("signature"):
            errors.append("publish: pack has no attestation signature "
                          "(an unsigned pack cannot be installed)")
        if errors:
            raise PackPublishError(
                "publish refused for %s %s: %s"
                % (pid, version, "; ".join(errors)))

        existing = self._packs.get(pid, {})
        if version in existing:
            raise PackAlreadyExistsError(
                "pack %s %s is already published (immutable)" % (pid, version))

        # planned -> live transition on first publish of this pack id.
        current = self._state.get(pid)
        if current is None or current == "planned":
            self._state[pid] = "live"
        elif current not in ("live",):
            raise PackLifecycleError(
                "cannot publish %s %s: pack lifecycle is '%s' (not planned/"
                "live)" % (pid, version, current))
        existing[version] = pack
        self._packs[pid] = existing
        self._emit("publish", "live", pid, version=version, actor=actor)
        return self._state[pid]

    # -- lifecycle state machine --------------------------------------------
    def transition(self, pack_id, target, actor=None):
        """Transition the pack's lifecycle to ``target`` if legal.

        planned -> live (publish), live -> paused (pause), paused -> live
        (resume), live|paused -> retired (retire, terminal). An illegal or
        terminal transition raises ``PackLifecycleError``.
        """
        if pack_id not in self._state:
            raise PackNotFoundError("unknown pack '%s'" % pack_id)
        current = self._state[pack_id]
        if target not in TRANSITIONS.get(current, set()):
            raise PackLifecycleError(
                "illegal transition '%s' -> '%s' for pack '%s'"
                % (current, target, pack_id))
        if current == target:
            raise PackLifecycleError(
                "pack '%s' is already '%s'" % (pack_id, target))
        self._state[pack_id] = target
        if target == "live" and current == "paused":
            self._emit("resume", "live", pack_id, actor=actor)
        elif target == "live":
            self._emit("publish", "live", pack_id, actor=actor)
        elif target == "paused":
            self._emit("pause", "paused", pack_id, actor=actor)
        elif target == "retired":
            self._emit("retire", "retired", pack_id, actor=actor)
        return target

    def pause(self, pack_id, actor=None):
        return self.transition(pack_id, "paused", actor=actor)

    def resume(self, pack_id, actor=None):
        return self.transition(pack_id, "live", actor=actor)

    def retire(self, pack_id, actor=None):
        return self.transition(pack_id, "retired", actor=actor)

    def state(self, pack_id):
        """Current lifecycle of a pack id; None when unknown."""
        return self._state.get(pack_id)

    def is_installable(self, pack_id):
        return self._state.get(pack_id) == "live"

    # -- reads ---------------------------------------------------------------
    def get(self, pack_id, version=None):
        """Resolve a published pack (optionally a specific version)."""
        versions = self._packs.get(pack_id)
        if not versions:
            raise PackNotFoundError("unknown pack '%s'" % pack_id)
        if version is None:
            # newest live-or-any published version
            return versions[max(versions, key=lambda v: [int(p) for p in
                                                         v.split(".")])]
        if version not in versions:
            raise PackNotFoundError("pack '%s' has no version %s"
                                    % (pack_id, version))
        return versions[version]

    def versions(self, pack_id):
        versions = self._packs.get(pack_id)
        if not versions:
            raise PackNotFoundError("unknown pack '%s'" % pack_id)
        return sorted(versions, key=lambda v: [int(p) for p in v.split(".")])

    def live_packs(self):
        """Catalog rows for packs whose current lifecycle is live."""
        rows = []
        for pid in sorted(self._state):
            if self._state[pid] != "live":
                continue
            try:
                doc = self.get(pid)
            except PackNotFoundError:  # pragma: no cover - defensive
                continue
            rows.append(self._row(pid, doc))
        return rows

    @staticmethod
    def _row(pid, doc):
        latest = doc.get("version")
        return {
            "id": pid,
            "version": latest,
            "name": doc.get("name"),
            "category": doc.get("category"),
            "upstream": doc.get("upstream"),
            "publisher": doc.get("publisher"),
            "lifecycle": "live",
            "description": doc.get("description"),
            "tags": doc.get("tags") or [],
        }

    def search(self, category=None, text=None, upstream=None):
        """Catalog search over live packs.

        Filters: ``category`` (exact closed category), ``text`` (case-
        insensitive substring over id/name/description/tags), ``upstream``
        (substring over the upstream URI). Returns a list of catalog rows.
        """
        results = []
        for row in self.live_packs():
            if category is not None and row["category"] != category:
                continue
            if upstream is not None and upstream.lower() \
                    not in (row["upstream"] or "").lower():
                continue
            if text is not None:
                haystack = " ".join(filter(None, [
                    row["id"], row["name"], row.get("description") or "",
                    " ".join(row["tags"])]))
                if text.lower() not in haystack.lower():
                    continue
            results.append(row)
        return results

    # -- catalog crossref -----------------------------------------------------
    def crossref(self, pack_id):
        """Dependency/dependent view of one pack id (CMR catalog crossref).

        ``dependencies`` = the (id, version) pins this pack declares;
        ``dependents`` = published packs that pin this pack as a dependency.
        """
        try:
            doc = self.get(pack_id)
        except PackNotFoundError:
            raise PackNotFoundError("unknown pack '%s'" % pack_id)
        deps = [dict(d) for d in (doc.get("dependencies") or [])]
        dependents = []
        for other_id, versions in self._packs.items():
            if other_id == pack_id:
                continue
            for other_doc in versions.values():
                for dep in (other_doc.get("dependencies") or []):
                    if dep.get("id") == pack_id:
                        dependents.append({
                            "id": other_id,
                            "version": other_doc.get("version"),
                        })
        # dedupe dependents by (id, version)
        seen = set()
        unique = []
        for entry in dependents:
            key = (entry["id"], entry["version"])
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        return {"id": pack_id, "dependencies": deps,
                "dependents": sorted(unique, key=lambda e: (e["id"],
                                                            e["version"]))}

    # -- consumption tracking -------------------------------------------------
    def record_install(self, tenant_id, pack_id, version, actor=None):
        """Record that ``tenant_id`` installed ``pack_id`` at ``version``.

        Consumption tracking (who installed which pack/version). The newest
        recorded version for a (tenant, pack) is the active one. Appends an
        ``install`` + ``consume`` event.
        """
        if not self.is_installable(pack_id):
            raise PackLifecycleError(
                "pack '%s' is '%s'; only live packs are installable"
                % (pack_id, self._state.get(pack_id)))
        try:
            doc = self.get(pack_id, version)
        except PackNotFoundError:
            raise
        # schema sanity for the exact version being installed
        errors = _schema_valid_errors(doc, self._schema,
                                      "install/%s@%s" % (pack_id, version)) \
            if self._schema else []
        if errors:
            raise PackRegistryError("install refused for %s %s: %s"
                                    % (pack_id, version, "; ".join(errors)))
        tenant_row = self._installs.setdefault(tenant_id, {})
        history = tenant_row.setdefault(pack_id, [])
        if history and history[-1] == version:
            raise PackRegistryError(
                "tenant '%s' already has pack %s %s active"
                % (tenant_id, pack_id, version))
        history.append(version)
        self._emit("install", "installed", pack_id, version=version,
                   tenant_id=tenant_id, actor=actor)
        self._emit("consume", "consumed", pack_id, version=version,
                   tenant_id=tenant_id, actor=actor)
        return version

    def active_version(self, tenant_id, pack_id):
        """Active (newest) installed version for a tenant+pack, or None."""
        return (self._installs.get(tenant_id, {}).get(pack_id) or [None])[-1]

    def previous_version(self, tenant_id, pack_id):
        """The version active before the current one, or None."""
        history = self._installs.get(tenant_id, {}).get(pack_id) or []
        return history[-2] if len(history) >= 2 else None

    def consumption(self, pack_id):
        """Who installed which version of ``pack_id`` (catalog consumption)."""
        rows = []
        for tenant_id, packs in sorted(self._installs.items()):
            if pack_id not in packs:
                continue
            for version in packs[pack_id]:
                rows.append({"tenantId": tenant_id, "pack": pack_id,
                             "version": version})
        return rows

    def consumption_by_tenant(self, tenant_id):
        """All installs recorded for one tenant, newest last per pack."""
        rows = []
        for pack_id, history in sorted(
                self._installs.get(tenant_id, {}).items()):
            for version in history:
                rows.append({"tenantId": tenant_id, "pack": pack_id,
                             "version": version})
        return rows

    # -- package internals ----------------------------------------------------
    @property
    def event_log(self):
        return self._log
