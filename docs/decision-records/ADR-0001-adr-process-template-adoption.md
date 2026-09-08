---
id: ADR-0001
status: accepted
date: 2026-09-08
deciders: [owner]
req: []
supersedes: []
---

# ADR-0001: ADR process + template adoption

## Status

`accepted` — ratified by issue #7 (golden rules + policy spine).

## Context

`agent-orchestrator` is a multi-tenant SaaS control plane built across eight
pillar phases (EPIC-00, issue #4). Each pillar will make architecture decisions
that are expensive to reverse (tenancy model, isolation boundary, guardrail
contracts, audit ledger shape). Without a durable decision-record method, those
decisions would be made silently in PRs and become unrecoverable later.

The fleet already has a proven ADR method in `kushin77/CMR`
(`docs/decision-records/`, CMR-109): filename `ADR-NNNN-<kebab-slug>.md`,
front-matter (`id`/`status`/`date`/`deciders`/`req`/`supersedes`), sections
Status · Context · Decision · Consequences, and a lifecycle of
proposed/accepted/superseded/deprecated. Issue #7 names that method as the
cannibalization source and mandates an ADR template plus reservations for every
pillar contract.

Two adaptations are needed for this product: (1) a `reserved` state so numbers
can be held in advance for pillar contracts that later issues will fill, and
(2) an explicit pointer from the template to the product's golden rules
(`AO-GR-*` in `docs/GOLDEN-RULES.md`) so decisions reference the rules they
satisfy.

## Decision

Adopt the ADR method in `docs/decision-records/`:

- Decisions live in `docs/decision-records/` as `ADR-NNNN-<kebab-slug>.md`,
  started from [`template.md`](template.md).
- Records use the CMR front-matter and the Status · Context · Decision ·
  Consequences sections; new decisions cite the golden rules (AO-GR-N) and any
  superseded ADRs.
- Add `reserved` as an **index-only** lifecycle state: pillar-contract numbers
  are held in the [README index](README.md) as placeholders headed
  `STATUS: reserved for <issue #>`, replaced by a full ADR when the owning issue
  lands. Reserved files never fabricate decided content.
- Numbers ADR-0002…ADR-0009 are reserved now for the eight pillar contracts
  (agent-registry, model-gateway, state-machine, guardrails, observability,
  identity/rbac, control-plane, autonomous-ops) per issue #7's
  "ADR-0001+ for all pillar contracts".

## Consequences

- **Positive:** durable, findable, reviewable decisions; pillar lanes have a
  reserved slot and a template so their contract ADRs land consistently; the
  golden-rules spine (issue #7) gets a decision-record home.
- **Negative:** a small authoring overhead — architecture changes must be
  recorded, not just merged.
- **Neutral:** CMR's numbering differs from ours (CMR ADR-0001 is its
  hub-control-plane decision); number spaces are per-repo and never assumed
  identical.

Follow-ups: pillar issues #9–#47 fill ADR-0002…ADR-0009; `make verify` ADR
validation lands with CI (issue #6).
