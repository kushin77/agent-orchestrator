# Provenance — paperclip skills adapter (issue #419)

This is the provenance record (GR-10, provenance doctrine) for the declarations
and the vocabulary this adapter introduces. The adapter **vendors no upstream
code**: every declaration is a *declaration plus a reference*, and the reference
is recorded here and in each declaration's own front-matter.

The verdict vocabulary follows the issue #145 harvest map: **AUTHORED** (written
here), **READY** (usable almost as-is), **PATTERN** (the shape is worth
re-implementing; the upstream machinery is not), **REFERENCE** (informs
doctrine, not structure).

## Sources referenced by the shipped declarations

| Declaration | Source repo | Source path | License | Verdict |
|---|---|---|---|---|
| `ticket-contract-read` | `kushin77/agent-orchestrator` | `docs/contracts/paperclip/ticket.schema.json` | MIT | AUTHORED |
| `mcp-tool-projection` | `kushin77/agent-orchestrator` | `gateway/mcp/tools.py` | MIT | AUTHORED |
| `sme-card-authoring` | `kushin77/CMR` | `onboarding/agent-profiles/role.schema.json` | internal (no LICENSE file in the checkout) | REFERENCE |
| `board-sync-plugin` | `kushin77/leaderboard` | `docker/worker-fleet/personas.yaml` | Proprietary (kushin77, All Rights Reserved) | PATTERN |
| `claim-gate-check` | `kushin77/agent-orchestrator` | `governance/dispatch/README.md` | MIT | AUTHORED |

The two `kushin77/CMR` and `kushin77/leaderboard` rows mirror the citations
already recorded in `../../../registry/profiles/seeds/PROVENANCE.md` and
`../../../docs/CANNIBALIZATION.md`; nothing here claims a source that file does
not already name.

## Upstream

Upstream is `paperclipai/paperclip` (product **paperclip.ing**), **MIT**,
`v2026.831.1` (2026-09-02), as recorded by EPIC #410. The adapter is written
against the frozen contracts under `../../../docs/contracts/paperclip/`;
**no upstream file is copied and no upstream package becomes a dependency**.

## What is authored here

- The registry document [`registry.json`](registry.json) and the closed-set
  discipline in [`registry.py`](registry.py).
- The projected view [`mcp_tools.json`](mcp_tools.json) and the derivation in
  [`projection.py`](projection.py) — derived from `gateway/mcp/`, not authored.
- The load gate in [`loader.py`](loader.py) and the declaration loader in
  [`frontmatter.py`](frontmatter.py).
- The five declarations under `library/` and `plugins/`, each carrying its own
  provenance block.

## Read `AGENTS.md` first

The repo's own instructions govern; this file is the provenance ledger for this
adapter, not a contract. The change honours the repo's rules: no secrets, no
`vendor/` edits, no unfinished markers in code, and no trailing whitespace.
