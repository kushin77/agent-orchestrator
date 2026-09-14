---
id: ticket-contract-read
kind: skill
name: Ticket contract reader
description: Read the frozen ticket contract v2 (ADR-0014) and its authority map before acting on a ticket.
provenance:
  repo: kushin77/agent-orchestrator
  path: docs/contracts/paperclip/ticket.schema.json
  license: MIT
  verdict: AUTHORED
requires:
  capabilities:
    - research
  mcp_tools:
    - kb.query
---

# Ticket contract reader

A **declaration plus a reference**, never a copy (GR-10). This skill declares no
behaviour of its own: it names the authority a reader must consult and the tool a
reader may use to reach it.

## What it declares

- The authority: `docs/contracts/paperclip/ticket.schema.json` and
  `docs/decision-records/ADR-0014-ticket-single-join-node-contract-v2.md`. The
  ticket is a projection; the one-writer map in `authority{}` is the join's single
  source of truth per field.
- The capability a reader must already hold: `research`.
- The projected MCP tool a reader may call: `kb.query`, which the tool authority
  `gateway/mcp/` declares callable and the projection carries.

## What it refuses

A load by a profile that does not grant `research` is refused at load, naming the
capability and the profile. The skill cannot grant it: the profile stays the
authority for what an agent may do.
