# Decision records (ADRs) — agent-orchestrator

Architecture decisions for the AI-agent-orchestration control plane are recorded
here as **Architecture Decision Records** (ADRs). The process is adopted from
`kushin77/CMR` `docs/decision-records/` (ADR method, CMR-109) and seeded by
issue #7. Recording a decision is how a proposal becomes durable: anyone (human
or agent) can see what was decided, why, and what it costs.

> **Path note.** This directory is the canonical ADR home (issue #7).
> Earlier foundation docs loosely referred to `docs/adr/`; the canonical path
> is `docs/decision-records/`.

## When to write an ADR

Write one when a decision:

- **Changes the architecture** — topology, pillars, the tenancy or isolation
  model, the governance model.
- **Is expensive to reverse** — or would be embarrassing to have made silently.
- **Comes up more than once** — the same question keeps being re-asked.
- **Supersedes an assumption** — a defaulted decision becomes ratified (or
  rejected) here.

A one-off implementation detail does **not** need an ADR. When in doubt: record
it — an ADR costs a page, an unrecorded architecture decision costs a migration.

## Lifecycle

| Status | Meaning |
|---|---|
| `proposed` | Written, not yet accepted. In review. |
| `accepted` | Ratified — the architecture follows it. |
| `superseded` | Replaced by a later ADR (which must name the one it supersedes). |
| `deprecated` | No longer followed; keep for history. |
| `reserved` | **Index-only.** A number held for a pillar-contract ADR a later issue will write. A reservation is a placeholder, not a decision — it carries no Context/Decision/Consequences. |

A decision moves `proposed → accepted` through review on the PR that introduces
the ADR. `accepted → superseded` only via a new ADR — never by editing the old
record out of existence. `reserved → proposed` when the owning pillar issue
writes the real ADR.

## Conventions

- **Filename:** `ADR-NNNN-<kebab-slug>.md` (zero-padded, sequential).
- **Front-matter:** `id`, `status`, `date`, `deciders`, `req`, optional
  `supersedes` — required on real (non-reserved) ADRs.
- **Sections:** Status · Context · Decision · Consequences (see the
  [template](template.md)).
- **Referencing:** cite an ADR as `ADR-0001` (link to the file). When a doc
  depends on a decision, link the ADR rather than restating it.
- **Reservations:** pillar-contract numbers are reserved in the index below and
  exist as short placeholder files headed `STATUS: reserved for <issue #>`.
  The owning issue replaces the placeholder with a full ADR from the template —
  reserved files must never contain fabricated decided content.
- **Superseding:** the new ADR lists `supersedes: ADR-000X`; the old record's
  status flips to `superseded` in the same change.

## Index

| ADR | Title | Status | Owned by |
|---|---|---|---|
| [ADR-0001](ADR-0001-adr-process-template-adoption.md) | ADR process + template adoption | accepted | issue #7 (this lane) |
| [ADR-0002](ADR-0002-agent-registry-contract.md) | Agent Registry & Profiling contract | reserved | issues #9–#14 (phase 1) |
| [ADR-0003](ADR-0003-model-gateway-contract.md) | Model Gateways contract | reserved | issues #15–#20 (phase 2) |
| [ADR-0004](ADR-0004-state-machine-execution-contract.md) | State-machine execution contract | reserved | issues #21–#25 (phase 3) |
| [ADR-0005](ADR-0005-security-guardrails-contract.md) | Security & guardrails contract | reserved | issues #26–#30 (phase 4) |
| [ADR-0006](ADR-0006-observability-telemetry-contract.md) | Observability / telemetry contract | reserved | issues #31–#34 (phase 5) |
| [ADR-0007](ADR-0007-identity-rbac-contract.md) | Tenant identity / RBAC contract | reserved | issues #35–#37 (phase 6) |
| [ADR-0008](ADR-0008-control-plane-contract.md) | Control plane / portal contract | reserved | issues #38–#42 (phase 7) |
| [ADR-0009](ADR-0009-autonomous-ops-contract.md) | Autonomous ops / governance contract | reserved | issues #43–#47 (phase 8) |
| [ADR-0010](ADR-0010-canonical-copy-ownership.md) | Canonical-copy ownership (MODEL/SME/SOLUTION-CLASSES trio + identity standards) | accepted | issue #47 (decision spike) |

The reserved set covers every pillar contract named in EPIC-00 (issue #4) and
[`ARCHITECTURE.md`](../ARCHITECTURE.md): agent-registry, model-gateway,
state-machine, guardrails, observability, identity/rbac, control-plane,
autonomous-ops. Standards those pillars will adopt come from
`kushin77/shared-governance` `GLOBAL_STANDARDS/**` (agent-action, agent-identity
JWT, agent-oidc-config, agent-task, telemetry-budget, approval-gate, egress
policy) — see [`GOLDEN-RULES.md`](../GOLDEN-RULES.md) provenance.

## Tooling

- New ADRs start from the [template](template.md).
- ADR front-matter and cross-references are validated as part of `make verify`
  once CI lands (issue #6); until then, ADR links are covered by the markdown
  link check in `make verify`.
