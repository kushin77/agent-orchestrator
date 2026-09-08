# AGENTS.md — governed consumer repo (instruction layering)

This repository embeds governed agents via the agent-orchestrator platform
(issue #41 consumer-repo template).  Agent instructions are **layered**, from
the most governed to the most local:

| Layer | File | Synced by platform |
|---|---|---|
| platform (governed) | `.agent-orchestrator/instructions/00-platform.md` | yes (auto) |
| agent-pack (governed) | `.agent-orchestrator/instructions/10-governed-agents.md` | yes (auto) |
| local (tenant-owned) | `.agent-orchestrator/instructions/20-local.md` | no |

The governed layers are read-only locally: `make verify` enforces parity with
the recorded drift manifest (a local edit is a failure) and `make sync`
refreshes them from the platform.  The local layer is where this repo's own
conventions live and is never overwritten.

## Instructions

Include the layers for any agent acting in this repo (lowest precedence
first — a local rule never overrides a governed rule):

@path .agent-orchestrator/instructions/00-platform.md
@path .agent-orchestrator/instructions/10-governed-agents.md
@path .agent-orchestrator/instructions/20-local.md
