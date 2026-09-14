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
| [ADR-0011](ADR-0011-session-fleet-transport.md) | Session-fleet transport — file mailbox now, A2A as the graduation target | accepted | issue #161 (M26) |
| [ADR-0012](ADR-0012-hermes-paperclip-boundary.md) | Hermes/Paperclip ownership boundary for the fleet | accepted | issue #303, amended by #317 (M26) |
| [ADR-0013](ADR-0013-paperclip-ing-integration.md) | Paperclip-ing integration mode — adopt the upstream CLI over HTTP (embed-vs-fork-vs-CLI) | accepted | issue #370 (M26) |
| [ADR-0014](ADR-0014-ticket-single-join-node-contract-v2.md) | The paperclip ticket is the single join node — ticket contract v2 (facets, authority) | accepted | issue #400 (EPIC #399, M26) |
| [ADR-0015](ADR-0015-routing-seam-single-authority.md) | Routing seam — the extracted tier-routing package is a policy source we map, not a runtime we couple | accepted | issue #426 (M28) |
| [ADR-0016](ADR-0016-paperclip-boundary-single-module.md) | One home for the paperclip boundary adapter — consolidate the parity adapters under `integrations/paperclip/` | accepted | issue #457 (M27) |
| [ADR-0017](ADR-0017-diagrams-authority-on-the-operator-surface.md) | The diagrams blueprint on the operator surface — a read-only `evidence[]` signal, never a new facet | accepted | issue #463 (EPIC #461, M27) |
| [ADR-0018](ADR-0018-codeidx-consumption-and-index-authority.md) | The two-index authority split + the no-re-derivation rule | accepted | issue #474 (EPIC #472) |
| [ADR-0022](ADR-0022-telemetry-exposition-authority-split.md) | The telemetry-exposition authority split + the no-second-dashboard rule | accepted | issue #495 (EPIC #494) |

> **Numbering gap `ADR-0019`–`ADR-0021`.** Those numbers are **CMR-hub** records
> cited from this repo with an explicit `CMR (fleet)` prefix
> (`governance/merge/README.md`: `ADR-0020` solo-dev merge posture, `ADR-0021`
> SME-reviewer-per-PR; `ADR-0019` the shared-governance dual-role record cited by
> #445). They are not this repo's to take. `ADR-0022` was allocated to the
> monitoring decision (#495) on the board — sibling lane #501 records *"`ADR-0022`
> is claimed by the monitoring decision `#495`"* and takes `ADR-0023` — so this
> repo's next free number is that one, and the gap is deliberate, not a
> reservation to fill. See the numbering note in
> [ADR-0022](ADR-0022-telemetry-exposition-authority-split.md).

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
