# gateway/catalog — gateway-owned module-catalog entries

The canonical module catalog for the ecosystem is the CMR module catalog
(`vendor/CMR/catalog/modules/`, issue #124), which is a pinned vendor
submodule and therefore not edited by gateway lanes. Issue #124 vendored the
DeepSeek, Ollama, Hermes and Paperclip module entries there.

This directory holds the **gateway-owned** entries that complete that catalog
without touching the pinned submodule or the registry lane's files:

| Entry | Module | Purpose |
|---|---|---|
| `modules/claude-anthropic/module.json` | `claude-anthropic` | The claude/anthropic provider module (issue #255), closing the 4-of-5 gap left by #124. |

The claude/anthropic entry is **referenced, not re-declared**, by the proxy
routing policy (`gateway/proxy/config/routing.yaml` `routingGroups.purebliss-team`)
which pins the `claude` agent to the `anthropic` provider.

## Provenance (GR-10)

The claude/anthropic entry is a **pattern** harvest from
`kushin77/gmail-agent` `src/agent/claude.ts` (Claude client: model tiers,
p-retry, agentic tool loop, zod output) — the same source already recorded for
the `anthropic` provider adapter in `docs/CANNIBALIZATION.md` §2.3. No source
file was copied; the `harvested_from` field records the source repo and path.
