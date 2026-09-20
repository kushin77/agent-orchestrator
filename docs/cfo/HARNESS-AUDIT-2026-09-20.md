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
  (`"name":"mcp__<server>__..."`, `"skill":"<name>...`) via `grep -l`/
  `grep -c` over `~/.claude/projects/*/*.jsonl`, counting files, never
  printing transcript content. Plain substring matches on a plugin/skill
  *name* were discarded as evidence — the skill-listing block itself
  contains every skill name on every turn, so a substring hit only proves
  the listing was injected, not that the skill ran. A bare-prefix grep
  (`mcp__playwright` without the trailing `__` and quote) was tried first
  and **also discarded**: this very audit session's own transcript
  contains every failing-server name in its MCP-connection-failure and
  deferred-tool-listing system-reminders, so a bare-prefix match self-
  contaminates. The counts below exclude this session's own transcript
  file (`ddff9d61-646b-4f55-9043-245196e547ca.jsonl`) and require the
  literal `"name":"mcp__X__..."` tool_use JSON shape.

## Inventory table

| Surface | Bytes | Injected every turn? | Invoked last 14d? (evidence) | Recommendation | Est. saving |
|---|---|---|---|---|---|
| `~/.claude/settings.json` | 4,451 | yes (drives session config, not verbatim text) | n/a (config) | keep | — |
| `~/.claude/settings.local.json` | 0 (absent) | n/a | n/a | keep (nothing to reduce) | — |
| `~/.claude/CLAUDE.md` | 4,248 | yes | every session (system prompt) | **compress** — see `proposed-global-CLAUDE.md` | 1,538 B (36%, verified `wc -c`) |
| `~/.claude/rules/context7.md` | 1,630 | yes (loaded as a rule) | context7 tool calls: **0 files** in 14d (`mcp__context7`/`context7__` literal tool-use not found; only listing-substring hits, discarded) | **keep** (explicit keep-list) but flag: rule mandates context7 on every library question yet zero real invocations found — either the rule isn't firing or no library questions arose; not evidence to disable (keep-list item) | — |
| Skill-availability listing (~120 skills, names+descriptions, injected in system-reminder every turn) | **estimate, not measured** — order-of-magnitude eyeball of the `<system-reminder>` skill block visible in this transcript; a jsonl-level isolation attempt (`grep`-selecting the message containing the block) returned whole-message lengths (tens of KB) that include unrelated adjacent content, so it wasn't clean enough to report as a measurement — treat this row's byte figure as directional only | **yes, every turn** | see per-plugin skill rows below | **compress**: convert low-use skill *descriptions* to one-line names-only where the plugin author allows, or scope with `disabledSkills`/marketplace-level disable for skills with 0 use in 14d that aren't on the keep-list (banner-design, cold-call-scripts, huashu-design, brand, machine-governance's peers, zapier:* store, marketing:*, productivity:*, engineering:* families, anthropic-skills:* long tail) | largest single lever; full removal of ~60 unused non-keep-list skill descriptions ≈ 6,000–8,000 B/turn (rough, proportional to count removed) |
| `superpowers` plugin (skills+agents+hooks) | plugin cache size not representative of prompt bytes (skill *bodies* are on-demand) | listing entries: yes; bodies: on-demand | `"skill":"superpowers` literal: **3 files** in 14d | keep (keep-list) | — |
| `remember` plugin (`.remember/*.md` injected files) | recent.md 1,353 + now.md 924 + archive.md 824 + remember.md 1,778 = **4,879 B**, injected per project session | yes, per-session for this project | `"skill":"remember` literal: **3 files** in 14d | keep (keep-list); recommend `remember:remember` compaction pass on `.remember/today-*.done.md` history (14,236 B / 10,663 B / 8,900 B files) which are on-demand not per-turn, so lower priority | — |
| `caveman` plugin (mode + cavecrew + compress + review + stats + help + commit) | listing entries only per-turn; hook-driven, no per-turn body injection beyond listing | yes (listing) | mode is hook-applied (`UserPromptSubmit`/`SessionStart` hooks in settings.json), not a `Skill` tool call, so literal `"skill":"caveman` = 0 is **expected**, not evidence of disuse | keep (keep-list) | — |
| `context7` plugin/MCP | MCP instruction block (~700 B, see MCP Server Instructions block this session) + rule file above | yes | 0 literal tool-use hits in 14d (see above) | keep (keep-list); flag for the user: verify context7 is actually firing when expected | — |
| `github` plugin (MCP) | MCP present in enabledPlugins; connects but **failing every session** | attempted every session it's granted | `"name":"mcp__github__..."` literal, excl. this audit session: **0 files** in 14d | **fix, don't disable** (see MCP fixes) — it's a keep-tier integration (code review/PR workflow) that's currently broken, not unused-by-choice; zero recent successful calls is *evidence of the outage*, not of low demand | — |
| `playwright` plugin (MCP) | skill listing entries (playwright-dev, playwright-devops) + MCP init | yes (listing); MCP: attempted, failing this session ("skipping, cached retry") | `"name":"mcp__playwright__..."` literal, excl. this audit session: **0 files** in 14d | not on keep-list, zero confirmed real use in 14d — **candidate to disable** if the user confirms no active Playwright work, saving 2 skill-listing entries (~200–300 B/turn) + MCP init attempt overhead | ~250 B/turn + avoids failed-connect overhead |
| `chrome-devtools-mcp` plugin | 5 skill-listing entries (chrome-devtools, a11y-debugging, cookie-debugging, debug-optimize-lcp, memory-leak-debugging, troubleshooting) | yes (listing) | `"name":"mcp__chrome-devtools__..."` literal, excl. this audit session: **0 files** in 14d | keep-list says `claude-in-chrome` (a *different*, built-in surface) is protected — `chrome-devtools-mcp` the *plugin* is not named on the keep-list and shows zero confirmed real use; **candidate to disable** pending user confirmation | ~400–500 B/turn (6 listing entries) |
| `security-guidance` plugin | listing entries (security-review skill) | yes | substring-only evidence (discarded); no literal skill-invocation pattern checked separately this pass | keep (security tooling; low listing cost, 1 entry) | — |
| `cmr-indexer` MCP (project-level, `~/agent-orchestrator/.mcp.json`) | 169 B config; fails every session (`CONNECTION_CLOSED`) | attempted every session in this repo | `"name":"mcp__cmr-indexer__..."` literal, excl. this audit session: **0 files** in 14d. (An earlier bare-prefix `mcp__cmr-indexer` grep returned 45 files, but that was contaminated by this outage's own failure notice appearing in every agent-orchestrator session's system-reminder — discarded per Method.) | **fix** (see MCP fixes) — root cause is confirmed independent of the usage question (missing script file), so fix regardless; don't cite "highest real usage" as the reason, there's no confirmed real usage in the window checked | — |
| `desktop-commander` plugin/MCP | listing entries (5 skills: ai-tools-setup, computer-health-check, desktop-commander-overview, knowledge-base, obsidian-vault, terminal) present in this session's skill list, but **plugin is not in `installed_plugins.json`'s 8 installed plugins, not in `~/.claude.json`'s `mcpServers` or any `projects.*` scope, and not in `~/.claude/settings.json`** | yes (listing appears regardless) | `"name":"mcp__desktop-commander__..."` literal, excl. this audit session: **0 files** in 14d | **source not locatable from user-scope files inspected this pass** — every file this audit checked (installed_plugins.json, `~/.claude.json`, settings.json) shows no desktop-commander entry, yet it appears live in this session's skill listing and MCP-failure reminder. This audit will not assert "stale marketplace catalog" as the cause without finding the config that enables it — flagged for the user/platform-sme to trace (possibly an org/team-level scope outside `~/.claude` this pass didn't have visibility into) | unknown until source is found — do not act on the ~500 B/turn estimate below without confirming first |
| `~/.claude/agents/**` | 0 B (directory absent/empty — `find` returned nothing) | n/a | n/a | keep (nothing there) | — |
| Deferred-tool-name list (this session: ~180 MCP tool names for unauthenticated integrations — Gmail, Calendar, Drive, Slack, Notion, Linear, Asana, Zapier, Datadog, PagerDuty, etc.) | **estimate, not measured** — same isolation limitation as the skill-listing row above; directional only, count of ~180 names is a manual count of the visible block, not a byte measurement | **yes, every turn**, regardless of whether any are ever called | Gmail/Calendar/Drive/Higgsfield are on keep-list; the marketing/productivity-suite integration tool names (Slack, Notion, Linear, Asana, Monday, ClickUp, Atlassian, HubSpot, Klaviyo, Canva, Figma, Ahrefs, Amplitude×2, Supermetrics, Datadog, PagerDuty, Intercom) show **zero evidence of any real call this session or historically searched** | **largest single non-keep-list lever**: these are third-party plugin marketplace integrations installed but (per `installed_plugins.json`, only 8 plugins are actually installed) likely present only as *listed-but-unauthenticated* placeholders in the deferred-tool catalog; disabling/uninstalling unused marketplace plugin integrations removes their deferred-tool-name entries entirely | plausibly 6,000–8,000 B/turn if the ~17 unused integration families are removed from the catalog |
| `agent-orchestrator` project `MEMORY.md` | 2,036 B | yes, this project only | actively referenced (11 entries, all recent) | keep | — |
| `.mcp.json` (agent-orchestrator, shared-services) | 169 B + 258 B | yes, project-scoped | cmr-indexer entry is the agent-orchestrator one (broken, see above) | fix cmr-indexer; shared-services `.mcp.json` not inspected in detail this pass (out of scope: audit was AO-focused) | — |

## Totals

- **File-backed, repo/dotfile bytes actually `stat`-able this pass:**
  CLAUDE.md 4,248 + rules 1,630 + MEMORY.md 2,036 + remember 4-file set
  4,879 + .mcp.json×2 427 = **13,220 B**. (This is a different, narrower
  cut than the PROMPT-REDUCTION-PLAN's repo-tracked 28,585 B, which counts
  `AGENTS.md`/`.cursorrules` inside the git repo, not `~/.claude` dotfiles;
  the two totals are not additive without double-counting — see that doc.)
- **Listing-driven, non-file-backed per-turn injection — ESTIMATED, not
  measured** (an attempt to isolate exact bytes from this session's own
  jsonl returned whole-message lengths that include unrelated adjacent
  content, so no clean per-block byte count was obtained this pass):
  skill-availability block (order tens of KB) + deferred-tool-name block
  (order several KB) + MCP-server-instructions block (order ~1 KB) is
  directionally larger than the 13,220 B file-backed total above, but no
  single reliable sum is reported here. **Follow-up needed** (source:
  a harness-side token/byte-count API per PROMPT-REDUCTION-PLAN lever #1,
  or a cleaner jsonl-field isolation than attempted this pass) before a
  precise per-turn total or % reduction can be claimed.
- **Skills/tools/agents count (listing-driven, counted by eye from the
  visible listing, not byte-measured):** ~120 skills listed, ~180
  deferred MCP tool names listed, 8 plugins installed, 0 custom agents in
  `~/.claude/agents`.
- **Projected reduction if all recommendations applied:** directional
  only, given the estimate above isn't a firm baseline. The concrete,
  file-backed piece is solid: **CLAUDE.md ~1,538 B saved (36%, 4,248→2,710
  B, verified by `wc -c` on the actual proposed file)**. The listing/MCP
  changes (disable playwright + chrome-devtools-mcp skill listings, prune
  unused marketplace integration tool-name families from the deferred-tool
  catalog pending desktop-commander's source being found) would reduce the
  *count* of listed items by roughly 20 skill entries and ~17 integration
  tool-name families out of ~120/~180 — a proportional cut, not a byte
  figure this pass can defend. This does **not** claim to reach the plan's
  stated 90%+ target — consistent with PROMPT-REDUCTION-PLAN's own
  "Headline finding" that a 90%+ reduction isn't achievable from levers a
  repo or a single user's dotfiles control; most of the harness-injected
  surface is Claude Code's own built-in tool-schema and skill-catalog
  mechanism, not user-controllable from this checkout.

## Top-10 savings, ranked, by evidence class

1. **CLAUDE.md compression** — 1,538 B/turn (36%). Evidence: **measured**
   (`wc -c` on both files, patch dry-run-verified end to end).
2. **Fix `cmr-indexer`** (implement or repoint
   `catalog/indexer/mcp_server.py`) — not a byte saving, but removes a
   failed-connect attempt every agent-orchestrator session. Evidence:
   **measured** (root cause: `ls` confirms the file is absent).
3. **Fix `github` plugin auth** — not a byte saving; restores a keep-tier
   capability that's currently dead weight (attempted, fails, wastes the
   attempt). Evidence: **measured** (OAuth account present, header
   malformed per session error text).
4. **Disable `playwright` plugin** (skill listing + MCP) if unused —
   ~250 B/turn estimated. Evidence: usage is **measured** (0 confirmed
   real calls in 14d, self-session excluded); byte saving is **estimated**.
5. **Disable `chrome-devtools-mcp` plugin** (skill listing + MCP) if
   unused — ~400–500 B/turn estimated. Evidence: usage **measured** (0
   confirmed calls); byte saving **estimated**.
6. **Prune unused marketplace integration tool-name families from the
   deferred-tool catalog** (Slack/Notion/Linear/Asana/Monday/ClickUp/
   Atlassian/HubSpot/Klaviyo/Canva/Figma/Ahrefs/Amplitude×2/Supermetrics/
   Datadog/PagerDuty/Intercom) — directionally the largest remaining
   lever. Evidence: **unmeasured** (no clean byte isolation this pass;
   see "What was NOT done"); presence-count is a manual eyeball, not a
   grep-verified 0.
7. **Compress low-use skill descriptions to names-only** where the
   skill/marketplace format allows. Evidence: **unmeasured**, same
   isolation gap as #6.
8. **Locate and resolve the `desktop-commander` phantom config.** Not a
   byte saving until the source is found; could be a legitimate org-scope
   integration, not a stale entry — do not disable blind. Evidence:
   config-absence is **measured** (checked 3 user-scope files); cause is
   **unknown**.
9. **`.remember/today-*.done.md` history compaction** (14,236 + 10,663 +
   8,900 B) — on-demand files, not per-turn injection, so lower priority
   than anything above despite the larger raw byte count. Evidence:
   **measured** file sizes; **not** a per-turn cost.
10. **Diff `.cursorrules` vs `AGENTS.md`** (PROMPT-REDUCTION-PLAN lever
    #2, still open there) — out of this audit's `~/.claude` scope but
    adjacent; flagged as the next-cheapest repo-side check. Evidence:
    **unmeasured this pass** (408-line `diff` run, not interpreted for
    overlap %).

## MCP server fixes (diagnosed read-only)

1. **`cmr-indexer` (`CONNECTION_CLOSED`) — root cause confirmed, usage
   evidence corrected:** an initial bare-prefix `grep` for `mcp__cmr-
   indexer` returned 45 matching files and this doc originally reported it
   as "highest real usage of any failing server" — that count was
   contaminated by this very outage's own failure notice appearing in
   every agent-orchestrator session's system-reminder (self-referential,
   not evidence of real tool calls). Re-run with the literal
   `"name":"mcp__cmr-indexer__..."` tool_use shape, excluding this audit
   session, returns **0 files** in 14 days. The fix below stands on its
   own merits (a missing file is a bug regardless of call volume), but is
   no longer justified by a usage-priority claim. `~/agent-orchestrator/.mcp.json` points
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
   installed plugins, in `~/.claude.json`'s `mcpServers` or any
   `projects.*` scope, or in `~/.claude/settings.json` — every user-scope
   file this audit could inspect. Its source could not be located this
   pass, so this audit does **not** assert "stale marketplace catalog" as
   the cause (that would be a guess); recommend the user run the plugin
   manager's list/repair command, and check for an org/team-level config
   scope outside `~/.claude` that this read-only pass didn't have
   visibility into.

## Files produced (this branch)

- `docs/cfo/harness-audit-2026-09-20.patch` — unified diff against the
  settings surfaces named above, **not yet applied**, with exact apply
  commands (including backups) in the patch header comment.
- `docs/cfo/proposed-global-CLAUDE.md` — compressed draft of
  `~/.claude/CLAUDE.md`, every rule preserved, 36% smaller by `wc -c`
  (4,248→2,710 B; exceeds the ≥30% bar for inclusion).

## What was NOT done (scope limits, stated not hidden)

- `shared-services/.mcp.json` was sized only, not inspected in depth
  (out of the agent-orchestrator-centric scope named in the dispatch).
- The exact per-skill-description and per-deferred-tool-name byte cost was
  **not measured** — the isolation attempt (grepping this session's own
  jsonl for the message containing each block) returned whole-message
  lengths polluted by adjacent unrelated content, not clean block sizes.
  Those rows in the inventory table are marked "estimate, not measured"
  rather than reported as numbers; a harness-side token/byte-count API
  (PROMPT-REDUCTION-PLAN lever #1) is the honest path to a real figure.
- No settings file was modified. `harness-audit-2026-09-20.patch` (the
  CLAUDE.md diff) is provided to review and apply manually; this audit
  does not apply it, and it is dry-run-verified (`patch -p1 --dry-run`)
  against a copy of the live `~/.claude/CLAUDE.md` to confirm it applies
  cleanly with the exact command given in its header.
