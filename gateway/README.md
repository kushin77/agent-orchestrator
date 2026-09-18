# gateway — Model Gateways (pillar 2, phase 2)

Owner lane: **gateway**. See [`../docs/EXECUTION-PLAN.md`](../docs/EXECUTION-PLAN.md).

## Purpose

Decoupled multi-provider proxy/router for commercial model providers (Claude,
DeepSeek, Copilot, Gemini, local Ollama, …): routing, fallback, key handling,
quotas, budgets/FinOps enforcement (EPIC-00, issue #4).

## Planned contents (phase 2, issues #15–#20)

- Provider adapters + unified request/response contract.
- Routing / fallback / retry policy.
- Key management and per-tenant quotas/budgets.

## Status

Placeholder scaffold from issue #5. No implementation yet.

## Live sync

`gateway/sync/` (issue #889, lane L10) is a real live-read module, not a
cached snapshot: `sync.live.project` re-reads
`gateway/catalog/modules/<id>/module.json` off disk on every call and, when
given a live `health.monitor.HealthMonitor`, asks it directly for
`is_healthy(provider, model)` — it never fabricates a reachability verdict.
It serves the registered head agent (hermes, the L10 registered head) its
catalog identity plus current reachability (`reachable` / `unreachable` /
`unknown`, the last only when no monitor is wired). An unregistered/unknown
catalog module id is refused by name (`UnknownCatalogModule`), never silently
reported unreachable. Tests: `gateway/sync/tests/` (offline fixtures, no
network), including the negative control for an unknown module id.
