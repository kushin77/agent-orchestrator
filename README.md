# agent-orchestrator

**Dedicated AI-agent-orchestration service control plane.** A multi-tenant SaaS
control plane that **organizes, governs, and manages commercial AI agents**
(Claude, DeepSeek, Copilot, Gemini, local Ollama, …) for enterprises. EPIC-00
blueprint: [issue #4](https://github.com/kushin77/agent-orchestrator/issues/4).

- A tenant defines its **agent org**: roles, SME personas, capabilities,
  boundaries.
- The platform owns agent **identity, profiles, model routing/gateways,
  guardrails/DLP, budgets/FinOps, memory, execution state, audit,
  observability**.
- Consumers integrate via **REST API + SDK + MCP**; admins use the
  **control-plane portal**.

## Architecture (five pillars)

| Pillar | Dir | Phase |
|--------|-----|-------|
| Agent Registry & Profiling | `registry/` | 1 |
| Model Gateways | `gateway/` | 2 |
| State-machine execution | `engine/` | 3 |
| Security & guardrails | `guardrails/` | 4 |
| Observability | `telemetry/` | 5 |
| Tenant identity/RBAC (cross-cutting) | `identity/` | 6 |
| Control plane / portal (cross-cutting) | `control-plane/` `portal/` | 7 |
| Autonomous ops/governance (cross-cutting) | — | 8 |

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Repository layout

- **Pillar source dirs** — `registry/` `gateway/` `engine/` `guardrails/`
  `telemetry/` `identity/` `control-plane/` `portal/` `infra/` (each holds a
  tracked `README.md` placeholder describing what lands there).
- **`docs/`** — architecture, execution plan, governance.
- **`scripts/`** — repo tooling and gate scripts.
- **`vendor/CMR`** — pinned `kushin77/CMR` hub submodule (read-only reference;
  do not edit).
- **`.research/`** — local cannibalization scratch (gitignored; never commit).

## Governance

- **Default branch:** `master` (protected by convention; every change lands via
  a PR).
- **Canonical doctrine:** [`AGENTS.md`](AGENTS.md) → `CLAUDE.md`/`.cursorrules`
  are thin mirrors. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and
  [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md).
- **Roadmap:** GitHub issues board; every change tracked in an issue and closed
  with evidence (GR-2).

## Gate of record

```bash
make verify   # shell syntax + YAML + JSON + docs + secrets (honest, no-false-green)
make help     # list all targets
```

`make verify` is the gate of record until CI lands (issue #6). Run it before
every PR and merge; paste its output as evidence.

## Status

- Phase 0 foundations in progress. EPIC-00 (issue #4) stays open until all
  phases are closed and the product is live flag-gated OFF by default.
- Extraction history: `MIGRATION_NOTES.md`, `VALIDATION.md` (legacy artifacts).

