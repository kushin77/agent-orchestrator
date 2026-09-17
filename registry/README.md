# registry — Agent Registry & Profiling (pillar 1, phase 1)

Owner lane: **registry**. See [`../docs/EXECUTION-PLAN.md`](../docs/EXECUTION-PLAN.md).

## Purpose

Declarative schemas and lifecycle for the agent org the control plane manages:
system prompts, tools, constraints, and the org/persona/capability/boundary
definitions a tenant declares (EPIC-00, issue #4).

## Planned contents (phase 1, issues #9–#14)

- Declarative agent / persona / org schemas (prompts, tools, constraints,
  capabilities, boundaries).
- Registry CRUD + validation + versioning.
- Profiling: capability discovery, metadata, agent inventory.

## Status

Placeholder scaffold from issue #5. No implementation yet.

## Live sync

`registry/sync/` (issue #889, lane L10) validates and registers the **head
agent** (hermes): `sync.live.register_head_agent` reads the real, committed
seed profile (`registry/profiles/seeds/hermes.1.0.0.yaml`) and persona card
(`registry/personas/cards/hermes.yaml`) off disk, validates each against its
schema plus the live platform catalog (loading
`registry/profiles/validate.py` and `registry/personas/registry.py` — the
existing validators, never re-implemented), and, only when both pass, appends
one `register` event to the append-only hash-chained event log
(`registry/events/event_log.py`). A persona card missing a required
head-of-org identity field (`id` / `name`) is refused by name before anything
is appended, and a card/profile that fails schema or catalog-membership
validation is refused with the real validator's own error text. Tests:
`registry/sync/tests/` (offline; validates the real committed hermes files,
plus negative controls for a missing identity field, a missing seed and an
unknown capability).
