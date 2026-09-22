# gateway/sync — live head-agent registration + reachability projection (issue #889)

Answers one question, live: is the registered head agent (hermes) present in
the gateway catalog, and is it currently reachable? Reads the two real gateway
stores — the provider catalog (`gateway/catalog/modules/<id>/module.json`) and
the health monitor (`gateway/health`) — and re-reads/re-queries both on every
call. Never a cached or hand-written snapshot.

## Layout

| File | Role |
|---|---|
| [`__init__.py`](__init__.py) | public surface: re-exports `sync.live.project` and `UnknownCatalogModule` |
| [`live.py`](live.py) | the projection: reads the catalog file from disk and queries an injected `health.monitor.HealthMonitor` |
| [`tests/`](tests/) | projection coverage, including the unregistered-module refusal |

## Usage

```python
from sync.live import project, UnknownCatalogModule
```

An unregistered/unknown catalog module id is refused **by name**
(`UnknownCatalogModule`), never silently reported as unreachable. When no
live monitor is wired, reachability is honestly reported `"unknown"` rather
than fabricated.

## Related

Issue #889, lane L10.
