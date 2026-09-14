# gateway/catalog — gateway-owned module-catalog entries

The canonical module catalog for the ecosystem is the CMR module catalog
(`vendor/CMR/catalog/modules/`, issue #124), which is a pinned vendor
submodule and therefore not edited by gateway lanes. Issue #124 vendored the
DeepSeek, Ollama, Hermes and Paperclip module entries there.

This directory holds the **gateway-owned** entries that complete that catalog
without touching the pinned submodule or the registry lane's files:

| Entry | Module | Provider | Purpose |
|---|---|---|---|
| `modules/claude-anthropic/module.json` | `claude-anthropic` | `anthropic` | The claude/anthropic provider module (issue #255), closing the 4-of-5 gap left by #124. |
| `modules/deepseek/module.json` | `deepseek` | `deepseek` | The DeepSeek provider module (issue #349). |
| `modules/openai/module.json` | `openai` | `openai` | The OpenAI/Copilot/GPT provider module (issue #349). |
| `modules/gemini/module.json` | `gemini` | `gemini` | The Google Gemini provider module (issue #349). |
| `modules/ollama/module.json` | `ollama` | `ollama` | The local Ollama provider module (issue #349). |
| `modules/hermes/module.json` | `hermes` | `hermes` | The Hermes local agent-service provider module (issue #349). |
| `modules/paperclip/module.json` | `paperclip` | `paperclip` | The vendored Paperclip planning/status-report provider module (issue #349). |

The claude/anthropic entry is **referenced, not re-declared**, by the proxy
routing policy (`gateway/proxy/config/routing.yaml` `routingGroups.purebliss-team`)
which pins the `claude` agent to the `anthropic` provider.

## Parity rule (fail-closed, issue #349)

Every **registered provider adapter** must have exactly one catalog module here,
and every catalog module here must name a registered provider. The two sets are
kept in lock-step in **both** directions:

- a registered provider in `../providers/registry.py` (`PROVIDER_NAMES`) with no
  catalog module is a **failure** — a shipped adapter with no catalog entry; and
- a catalog module whose declared provider is not registered is a **failure** —
  a catalog entry with no adapter behind it.

The module declares the adapter it backs through `distribution.package`, the
Python package that implements it (`gateway.providers.<provider>`); the provider
id is that suffix. This is why the module id need not equal the provider id
(the `claude-anthropic` module backs the `anthropic` provider). The registered
provider set is read from the registry, never derived by globbing filenames, so
shared plumbing (`base.py`, `contract.py`, `registry.py`, ...) is correctly
excluded.

`scripts/check-gateway-catalog-parity.sh` enforces this rule and is wired into
`make verify` as the `gateway-catalog-parity` check. It is tri-state
(0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) and proves its own negative control: it
mutates a copy of the module set in both directions (removing a module, and
adding an orphan module) and requires the validator to refuse each mutant — a
check that cannot fail is a formality.

## Provenance (GR-10)

The claude/anthropic entry is a **pattern** harvest from
`kushin77/gmail-agent` `src/agent/claude.ts` (Claude client: model tiers,
p-retry, agentic tool loop, zod output) — the same source already recorded for
the `anthropic` provider adapter in `docs/CANNIBALIZATION.md` §2.3. No source
file was copied; the `harvested_from` field records the source repo and path.
