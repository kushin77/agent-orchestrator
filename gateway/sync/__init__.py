"""gateway/sync — live head-agent registration + reachability projection (issue #889).

Reads the two real gateway stores — the provider catalog
(``gateway/catalog/modules/<id>/module.json``) and the health monitor
(``gateway/health``) — and serves one live document answering "is the
registered head agent (hermes) present in the catalog, and is it currently
reachable?". Never a cached or hand-written snapshot: every call re-reads the
catalog file from disk and re-queries the injected ``HealthMonitor``.

Public surface: :func:`live.project`.

---knowledge---
module_id: gateway.sync
system: gateway
app: sync
solution_class: class
patterns: [package-contract, public-surface, delegate-never-re-derive]
derives_from: null
owner_sme: sync-sme
tier: L0
interfaces: [project]
invariants: "every call re-reads the catalog from disk and re-queries the injected monitor; never a cached or hand-written snapshot"
gotchas: ""
related: ["#889"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from sync.live import UnknownCatalogModule, project

__all__ = ["project", "UnknownCatalogModule"]
