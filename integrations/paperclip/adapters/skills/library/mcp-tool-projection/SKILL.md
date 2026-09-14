---
id: mcp-tool-projection
kind: skill
name: MCP tool projection reader
description: Read the projected MCP tool surface derived from gateway/mcp/ instead of a hand-maintained tool list.
provenance:
  repo: kushin77/agent-orchestrator
  path: gateway/mcp/tools.py
  license: MIT
  verdict: AUTHORED
requires:
  capabilities:
    - research
  mcp_tools:
    - code.search
---

# MCP tool projection reader

A **declaration plus a reference** (GR-10). The callable tool set is owned by the
tool authority `gateway/mcp/`; this skill points a reader at the derived view
(`integrations/paperclip/adapters/skills/mcp_tools.json`) rather than at a list anyone
maintains by hand.

## What it declares

- The tool authority: `gateway/mcp/tools.py` (`build_registry`) and the closed
  vocabulary `MCP_ALLOWLIST_KEYS` in `gateway/mcp/model.py`.
- The capability a reader must already hold: `research`.
- The projected MCP tool a reader may call: `code.search`.

## What it refuses

A load by a profile without `research` is refused at load. A requirement for a
tool that the authority declares callable but the projection omits is a FAIL
against the projection, not a silent success — the projection must be a view, not
a second allowlist.
