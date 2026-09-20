# Harness-Injected Prompt Audit — 2026-09-20

Read-only audit of the per-turn harness-injected surface for user
kushin77's Claude Code setup. Baseline and lever framing come from
`docs/cfo/PROMPT-REDUCTION-PLAN.md` (branch `issue-cfo-office-finops`),
which already established the repo-tracked baseline is **28,585 B**
(`AGENTS.md` 24,821 + `CLAUDE.md` 1,865 + `.cursorrules` 1,899) and that
the harness-injected surface (skill listing, MCP instruction blocks,
deferred-tool names) is larger still and not repo-measurable. This audit
inventories that harness-side surface directly, with cited byte counts,
and proposes a concrete change set. **No files under `~/.claude`,
`~/.copilot`, or any `settings.json` were modified** — this is read-only;
the patch below is a diff to *apply*, not one already applied.

## Method

- File sizes: `wc -c` on the actual files.
- "Injected every turn": yes = appears in the system prompt/reminders on
  every turn regardless of use (e.g. skill-availability listing, MCP
  instruction blocks, CLAUDE.md/rules); on-demand = only loaded when a
  tool/skill is actually invoked (skill *bodies*, agent defs); no = static
  config that never enters the prompt (marketplace cache, plugin binaries).
- "Invoked in last 14 days": evidence is **tool-call name matches**
  (`mcp__<server>__*`, `"skill":"<name>...`) via `grep -l`/`grep -c` over
  `~/.claude/projects/*/*.jsonl`, counting files, never printing transcript
  content. Plain substring matches on a plugin/skill *name* were discarded
  as evidence — the skill-listing block itself contains every skill name
  on every turn, so a substring hit only proves the listing was injected,
  not that the skill ran. Only literal tool-invocation JSON keys count.

## Inventory table

| Surface | Bytes | Injected every turn? | Invoked last 14d? (evidence) | Recommendation | Est. saving |
|---|---|---|---|---|---|
| `~/.claude/settings.json` | 4,451 | yes (drives session config, not verbatim text) | n/a (config) | keep | — |
| `~/.claude/settings.local.json` | 0 (absent) | n/a | n/a | keep (nothing to reduce) | — |
| `~/.claude/CLAUDE.md` | 4,248 | yes | every session (system prompt) | **compress** — see `proposed-global-CLAUDE.md` | ~1,900 B (~45%) |
| `~/.claude/rules/context7.md` | 1,630 | yes (loaded as a rule) | context7 tool calls: **0 files** in 14d (`mcp__context7`/`context7__` literal tool-use not found; only listing-substring hits, discarded) | **keep** (explicit keep-list) but flag: rule mandates context7 on every library question yet zero real invocations found — either the rule isn't firing or no library questions arose; not evidence to disable (keep-list item) | — |
| Skill-availability listing (~120 skills, names+descriptions, injected in system-reminder every turn) | ~14,900 B this session (measured: the `<system-reminder>` skill block in this transcript) | **yes, every turn** | see per-plugin skill rows below | **compress**: convert low-use skill *descriptions* to one-line names-only where the plugin author allows, or scope with `disabledSkills`/marketplace-level disable for skills with 0 use in 14d that aren't on the keep-list (banner-design, cold-call-scripts, huashu-design, brand, machine-governance's peers, zapier:* store, marketing:*, productivity:*, engineering:* families, anthropic-skills:* long tail) | largest single lever; full removal of ~60 unused non-keep-list skill descriptions ≈ 6,000–8,000 B/turn (rough, proportional to count removed) |
| `superpowers` plugin (skills+agents+hooks) | plugin cache size not representative of prompt bytes (skill *bodies* are on-demand) | listing entries: yes; bodies: on-demand | `"skill":"superpowers` literal: **3 files** in 14d | keep (keep-list) | — |
| `remember` plugin (`.remember/*.md` injected files) | recent.md 1,353 + now.md 924 + archive.md 824 + remember.md 1,778 = **4,879 B**, injected per project session | yes, per-session for this project | `"skill":"remember` literal: **3 files** in 14d | keep (keep-list); recommend `remember:remember` compaction pass on `.remember/today-*.done.md` history (14,236 B / 10,663 B / 8,900 B files) which are on-demand not per-turn, so lower priority | — |
| `caveman` plugin (mode + cavecrew + compress + review + stats + help + commit) | listing entries only per-turn; hook-driven, no per-turn body injection beyond listing | yes (listing) | mode is hook-applied (`UserPromptSubmit`/`SessionStart` hooks in settings.json), not a `Skill` tool call, so literal `"skill":"caveman` = 0 is **expected**, not evidence of disuse | keep (keep-list) | — |
| `context7` plugin/MCP | MCP instruction block (~700 B, see MCP Server Instructions block this session) + rule file above | yes | 0 literal tool-use hits in 14d (see above) | keep (keep-list); flag for the user: verify context7 is actually firing when expected | — |
| `github` plugin (MCP) | MCP present in enabledPlugins; connects but **failing every session** | attempted every session it's granted | `mcp__github` literal: **5 files** in 14d (some successful calls before the current auth break, or partial) | **fix, don't disable** (see MCP fixes) | — |
| `playwright` plugin (MCP) | skill listing entries (playwright-dev, playwright-devops) + MCP init | yes (listing); MCP: attempted, failing this session ("skipping, cached retry") | `mcp__playwright` literal: **1 file** in 14d | low use; not on keep-list — **candidate to disable** if the user confirms no active Playwright work, saving 2 skill-listing entries (~200–300 B/turn) + MCP init attempt overhead | ~250 B/turn + avoids failed-connect overhead |
| `chrome-devtools-mcp` plugin | 5 skill-listing entries (chrome-devtools, a11y-debugging, cookie-debugging, debug-optimize-lcp, memory-leak-debugging, troubleshooting) | yes (listing) | `mcp__chrome-devtools` literal: **1 file** in 14d | keep-list says `claude-in-chrome` (a *different*, built-in surface) is protected — `chrome-devtools-mcp` the *plugin* is not named on the keep-list and shows minimal use; **candidate to disable** pending user confirmation | ~400–500 B/turn (6 listing entries) |
| `security-guidance` plugin | listing entries (security-review skill) | yes | substring-only evidence (discarded); no literal skill-invocation pattern checked separately this pass | keep (security tooling; low listing cost, 1 entry) | — |
| `cmr-indexer` MCP (project-level, `~/agent-orchestrator/.mcp.json`) | 169 B config; fails every session (`CONNECTION_CLOSED`) | attempted every session in this repo | `mcp__cmr-indexer` literal: **45 files** in 14d (highest real usage of any failing server — this one matters) | **fix** (see MCP fixes) — this is the most-used broken server, prioritize | — |
| `desktop-commander` plugin/MCP | listing entries (5 skills: ai-tools-setup, computer-health-check, desktop-commander-overview, knowledge-base, obsidian-vault, terminal) present in this session's skill list, but **plugin is not in `installed_plugins.json`'s 8 installed plugins** | yes (listing appears regardless) | `mcp__desktop-commander` literal: **1 file** in 14d | **investigate**: listing shows it but install manifest doesn't list it — likely a stale marketplace-catalog entry surfacing skills for an uninstalled/partially-installed plugin; not a "disable" (nothing to disable — it's phantom), report to user | listing-entry removal if confirmed phantom: ~500 B/turn |
| `~/.claude/agents/**` | 0 B (directory absent/empty — `find` returned nothing) | n/a | n/a | keep (nothing there) | — |
| Deferred-tool-name list (this session: ~180 MCP tool names for unauthenticated integrations — Gmail, Calendar, Drive, Slack, Notion, Linear, Asana, Zapier, Datadog, PagerDuty, etc.) | measured this session: the deferred-tools `<system-reminder>` block is **~10,800 B** of tool names | **yes, every turn**, regardless of whether any are ever called | Gmail/Calendar/Drive/Higgsfield are on keep-list; the marketing/productivity-suite integration tool names (Slack, Notion, Linear, Asana, Monday, ClickUp, Atlassian, HubSpot, Klaviyo, Canva, Figma, Ahrefs, Amplitude×2, Supermetrics, Datadog, PagerDuty, Intercom) show **zero evidence of any real call this session or historically searched** | **largest single non-keep-list lever**: these are third-party plugin marketplace integrations installed but (per `installed_plugins.json`, only 8 plugins are actually installed) likely present only as *listed-but-unauthenticated* placeholders in the deferred-tool catalog; disabling/uninstalling unused marketplace plugin integrations removes their deferred-tool-name entries entirely | plausibly 6,000–8,000 B/turn if the ~17 unused integration families are removed from the catalog |
| `agent-orchestrator` project `MEMORY.md` | 2,036 B | yes, this project only | actively referenced (11 entries, all recent) | keep | — |
| `.mcp.json` (agent-orchestrator, shared-services) | 169 B + 258 B | yes, project-scoped | cmr-indexer entry is the agent-orchestrator one (broken, see above) | fix cmr-indexer; shared-services `.mcp.json` not inspected in detail this pass (out of scope: audit was AO-focused) | — |

## Totals

- **File-backed, repo/dotfile bytes actually `stat`-able this pass:**
  CLAUDE.md 4,248 + rules 1,630 + MEMORY.md 2,036 + remember 4-file set
  4,879 + .mcp.json×2 427 = **13,220 B**. (This is a different, narrower
  cut than the PROMPT-REDUCTION-PLAN's repo-tracked 28,585 B, which counts
  `AGENTS.md`/`.cursorrules` inside the git repo, not `~/.claude` dotfiles;
  the two totals are not additive without double-counting — see that doc.)
- **Listing-driven, non-file-backed per-turn injection measured directly
  from this session's system-reminders:** skill-availability block
  ≈14,900 B + deferred-tool-name block ≈10,800 B + MCP-server-instructions
  block ≈700 B = **≈26,400 B/turn**, every turn, regardless of use.
- **Skills/tools/agents count (listing-driven):** ~120 skills listed,
  ~180 deferred MCP tool names listed, 8 plugins installed, 0 custom
  agents in `~/.claude/agents`.
- **Projected reduction if all recommendations applied** (disable
  playwright + chrome-devtools-mcp skill listings, remove phantom
  desktop-commander listing entries, prune ~17 unused marketplace
  integration tool-name families from the deferred-tool catalog, compress
  CLAUDE.md ~45%): roughly **6,700–9,300 B off the ≈26,400 B/turn
  listing+MCP surface (≈25–35%)**, plus **~1,900 B off CLAUDE.md**. This
  does **not** reach the plan's stated 90%+ target — consistent with
  PROMPT-REDUCTION-PLAN's own "Headline finding" that a 90%+ reduction
  isn't achievable from levers a repo or a single user's dotfiles control;
  most of the remaining ≈17,000–19,000 B/turn is Claude Code's own
  built-in tool schemas and skill-catalog mechanism, not user-controllable
  from this checkout.

## MCP server fixes (diagnosed read-only)

1. **`cmr-indexer` (`CONNECTION_CLOSED`, highest real usage — 45 files in
   14d) — root cause confirmed:** `~/agent-orchestrator/.mcp.json` points
   at `catalog/indexer/mcp_server.py`, which **does not exist** in this
   checkout (`ls` confirms: no such file). The server process can't start,
   so the connection closes immediately. Fix is one of: (a) implement
   `catalog/indexer/mcp_server.py` if the indexer was meant to ship, or
   (b) remove/point the `.mcp.json` entry at the actual indexer script
   location if it lives elsewhere (e.g. `governance/knowledge/`). This is
   a project-file fix (`~/agent-orchestrator/.mcp.json`), not a
   `~/.claude`/settings change, so it's in scope for the patch below only
   as a *documented recommendation* — no code change made this pass
   (out of this audit's stated scope, which is the `~/.claude` surface).
2. **`github` plugin ("Authorization header is badly formatted"):** the
   plugin is enabled and OAuth account (`kushin77@gmail.com`) is present
   in `~/.claude.json`, so credentials exist but the header sent to the
   GitHub MCP endpoint is malformed — consistent with a stale or
   corrupted cached token. Fix: re-authenticate the GitHub plugin (Claude
   Code's `/mcp` reconnect flow or unlink+relink the plugin) rather than
   editing config by hand; do not hand-edit token storage.
3. **`playwright`, `chrome-devtools-mcp`, `desktop-commander` ("skipping
   connection, cached retry"):** these are in a 15-minute backoff after a
   prior failed connect attempt; the actual failure reason isn't visible
   in this session (no error text beyond "recent failure cached"). Root
   cause not diagnosable read-only from this session alone — needs either
   a retry with verbose MCP logging or `claude --mcp-debug` (or
   equivalent) at a time outside the backoff window. One structural
   inconsistency noted in passing: the `playwright` plugin's `.mcp.json`
   uses a bare `{"playwright": {...}}` shape while `context7`'s uses the
   wrapped `{"mcpServers": {"context7": {...}}}` shape — both may be
   valid depending on Claude Code's plugin-MCP loader, so this is flagged,
   not asserted as the cause. `desktop-commander` additionally does not
   appear in `~/.claude/plugins/installed_plugins.json`'s list of 8
   installed plugins even though its skills appear in the session
   listing — likely a stale marketplace-catalog entry for a partially
   installed/uninstalled plugin; recommend the user run the plugin
   manager's list/repair command to confirm install state.

## Files produced (this branch)

- `docs/cfo/harness-audit-2026-09-20.patch` — unified diff against the
  settings surfaces named above, **not yet applied**, with exact apply
  commands (including backups) in the patch header comment.
- `docs/cfo/proposed-global-CLAUDE.md` — compressed draft of
  `~/.claude/CLAUDE.md`, every rule preserved, ~45% smaller (exceeds the
  ≥30% bar for inclusion).

## What was NOT done (scope limits, stated not hidden)

- `shared-services/.mcp.json` was sized only, not inspected in depth
  (out of the agent-orchestrator-centric scope named in the dispatch).
- The exact per-skill-description byte cost wasn't individually measured
  for all ~120 skills — the ≈14,900 B skill-listing figure is a whole-block
  measurement from this session's own system-reminder, not a sum of
  per-skill `wc -c` (skill descriptions aren't separately file-backed at
  that granularity in a way this pass could `stat`).
- No settings file was modified. `proposed-settings.patch` is a diff to
  review and apply manually; this audit does not apply it.
