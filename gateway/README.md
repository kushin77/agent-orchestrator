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
