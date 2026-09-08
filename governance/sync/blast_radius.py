#!/usr/bin/env python3
"""Blast-radius engine (issue #44, acceptance criterion 3).

When a change is proposed to a shared/provenance asset, the blast-radius
engine computes the set of consumers and dependent assets that change would
affect — before anything is applied — so a change to a widely-consumed asset
can never silently break a consumer that vendors it.

The engine reads two offline inputs:

1. **Consumer provenance manifests** — each consumer's manifest lists the
   shared assets it vendors (asset_id -> pin). ``consumers_by_asset`` is built
   from these manifests; only a consumer recorded as vendoring a shared asset
   is ever reported as affected.
2. **A dependency catalog** (optional) — ``asset_id -> {"dependencies": [...]}``
   gives the transitive dependent closure: a change to ``X`` also affects
   every asset that (directly or transitively) depends on ``X``, and their
   consumers. Without a catalog the closure is just the changed asset itself.

Adapted (not copied) from `kushin77/CMR` `sync/blast-radius.sh` (resolve the
transitive dependent closure and enumerate every affected dependent before a
change lands; dry-run computes the closure without side effects) and the CMR
catalog crossref model consumed in `registry/packs/registry.py` (issue #40).
"""

from __future__ import annotations


class BlastRadiusError(ValueError):
    """The target asset is not a shared asset in any manifest/catalog."""


def _collect_manifests(manifests):
    """Return (consumers_by_asset, consumer_labels).

    ``consumers_by_asset`` maps shared asset_id -> sorted [consumer_id, ...].
    Duplicate consumer ids across manifests are refused (a silent override
    would hide consumers from the radius).
    """
    consumers_by_asset = {}
    consumer_labels = {}
    seen = set()
    for manifest in manifests:
        cid = manifest.consumer_id()
        if cid in seen:
            raise BlastRadiusError("duplicate consumer '%s' in manifest set"
                                   % cid)
        seen.add(cid)
        consumer_labels[cid] = manifest.consumer
        for asset_id in manifest.assets:
            consumers_by_asset.setdefault(asset_id, set()).add(cid)
    return ({k: sorted(v) for k, v in consumers_by_asset.items()},
            consumer_labels)


def _dependents_index(catalog):
    """Reverse the catalog: asset_id -> sorted set of direct dependents.

    The catalog is forward-declared (``asset -> [dependencies]``); blast
    radius walks dependents, so the index is the reverse edge map.
    """
    dependents = {}
    for asset_id, node in (catalog or {}).items():
        deps = (node or {}).get("dependencies") or []
        for dep in deps:
            dependents.setdefault(dep, set()).add(asset_id)
    return {k: sorted(v) for k, v in dependents.items()}


def _dependent_closure(target, dependents_index):
    """Breadth-first closure of every asset that depends on ``target``.

    Returns (closure, order) where ``closure`` is the sorted set of affected
    asset ids (target + transitive dependents) and ``order`` is the BFS
    discovery order with depth for reporting.
    """
    closure = {target}
    order = [(target, 0)]
    frontier = [target]
    depth = {target: 0}
    while frontier:
        current = frontier.pop(0)
        for dependent in dependents_index.get(current, []):
            if dependent not in depth:
                depth[dependent] = depth[current] + 1
                frontier.append(dependent)
                closure.add(dependent)
                order.append((dependent, depth[dependent]))
    return closure, order


class BlastRadiusEngine:
    """Compute the blast radius of a proposed shared-asset change."""

    def __init__(self, manifests, catalog=None):
        self.manifests = list(manifests)
        self.catalog = catalog or {}
        (self.consumers_by_asset,
         self.consumer_labels) = _collect_manifests(self.manifests)
        self._dependents = _dependents_index(self.catalog)

    def shared_assets(self):
        """All shared asset ids referenced by any consumer manifest."""
        return sorted(self.consumers_by_asset)

    def compute(self, asset_id, ref=None):
        """Return the blast-radius report for a proposed change to ``asset_id``.

        The target must be a shared asset in the manifest set (or a catalog
        node that consumers vendor) — a proposed change to an unknown asset is
        an error, not an empty report (no-false-green).
        """
        if asset_id not in self.consumers_by_asset and asset_id not in \
                self._dependents and asset_id not in self.catalog:
            raise BlastRadiusError(
                "asset '%s' is not a shared asset in any manifest or catalog"
                % asset_id)
        closure, order = _dependent_closure(asset_id, self._dependents)
        direct_consumers = self.consumers_by_asset.get(asset_id, [])
        affected_consumers = []
        for consumer in sorted(self.consumers_by_asset_union(closure)):
            vendored = sorted(a for a in closure
                              if consumer in (self.consumers_by_asset.get(a)
                                              or []))
            affected_consumers.append({
                "consumer": consumer,
                "repo": (self.consumer_labels.get(consumer) or {})
                .get("repo"),
                "affected_assets": vendored,
            })
        return {
            "changed_asset": asset_id,
            "ref": ref,
            "closure": sorted(closure),
            "closure_order": [{"asset": a, "depth": d} for a, d in order],
            "dependents": sorted(closure - {asset_id}),
            "direct_consumers": direct_consumers,
            "consumers": affected_consumers,
            "summary": {
                "assets_affected": len(closure),
                "dependents": len(closure) - 1,
                "consumers_affected": len(affected_consumers),
                "direct_consumers": len(direct_consumers),
            },
        }

    def consumers_affected_by(self, asset_id):
        """Consumers that vendor ``asset_id`` (the direct impact set)."""
        return list(self.consumers_by_asset.get(asset_id, []))

    def consumers_by_asset_union(self, closure):
        """Union of every consumer that vendors any asset in ``closure``."""
        union = set()
        for asset in closure:
            union.update(self.consumers_by_asset.get(asset, ()))
        return union
