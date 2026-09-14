# Fleet dashboard gap analysis — terminal TUI vs web single-pane-of-glass

Issue: [#330](https://github.com/kushin77/agent-orchestrator/issues/330) ·
Web SPoG roadmap: [#331](https://github.com/kushin77/agent-orchestrator/issues/331),
[#332](https://github.com/kushin77/agent-orchestrator/issues/332),
[#333](https://github.com/kushin77/agent-orchestrator/issues/333)

## Why this doc exists

The fleet's live view is `fleet/console.py` — a terminal dashboard that renders
one frame per refresh into the `fleet` tmux session. It is the right instrument
for the operator sitting at the machine, and it must stay that way. But "see the
whole orchestration end-to-end" means more than one pane on one host: a remote
operator, an auditor, or an org owner needs a surface a TUI structurally cannot
provide. This doc names the gap precisely, so the web single-pane-of-glass is
built against what a terminal *cannot* do, not against what it already does well.

## What the terminal TUI does today

`fleet/console.py` (with the live layout in `fleet/control.py`) renders, in one
bounded frame, the pure projection of `snapshot()`:

- **header** — repo, HEAD, uptime, attach/detach hint;
- **RUNGS** — brain / sister / monitor, each with pid, state, commit, beat age,
  health-coloured (green / yellow / red / dim);
- **ORDERS / DISPATCHES** — the operator↔brain traffic;
- **LIVE CLAIMS** — the dispatch ledger's held claims (bounded to 8 + tail);
- **WAVES** — the wave plan with closed / dispatched / pending glyphs;
- **RECENT EVENTS** — the last 8 `slog.jsonl` records;
- **WATCHDOG** — the last 5 watchdog verdict lines;
- a one-line status summary (`fleet: healthy · 3 rungs · 0 claims · …`).

Design invariants worth keeping: the frame is built by **pure `render(snap)`
functions** over a single `snapshot()`, it runs **without a TTY** (the tests
assert the exact strings), it **never crashes on missing state**, and redraw is
**bounded and non-flickering** (alternate screen, home + clear-to-end). Any web
surface that duplicates the projection must consume the same `snapshot()` shape,
not re-derive it.

## The gap — what a TUI structurally cannot do

| # | Capability | Why a terminal cannot provide it | Web issue |
|---|---|---|---|
| 1 | **Multi-tenant org roll-up** | One pane has no notion of tenant hierarchy; the roll-up in [#151](https://github.com/kushin77/agent-orchestrator/issues/151) aggregates across orgs, tenants, and lanes into a single view a tmux pane cannot lay out or scope. | [#332](https://github.com/kushin77/agent-orchestrator/issues/332) |
| 2 | **Historical timelines** | The TUI shows the last 8 events; `slog.jsonl` is append-only and multi-megabyte. A browser can page, scroll, and rewind the audit stream, which is a read model, not a bigger buffer. | [#332](https://github.com/kushin77/agent-orchestrator/issues/332) |
| 3 | **Filters and search** | A terminal has no query surface; a browser filters by tenant, lane, rung, severity, or issue before the frame is drawn. | [#332](https://github.com/kushin77/agent-orchestrator/issues/332) |
| 4 | **Live streaming to a browser** | `console.py` draws to a local TTY. A remote operator needs the same projection pushed over HTTP (SSE/WebSocket) with no tmux and no TTY. | [#331](https://github.com/kushin77/agent-orchestrator/issues/331) |
| 5 | **Access control / tenant scoping** | The TUI is a local tmux pane: whoever is at the host sees everything. A browser surface changes the threat model and must enforce `identity/rbac` so a tenant sees only its own org, lanes, and claims. | [#333](https://github.com/kushin77/agent-orchestrator/issues/333) |
| 6 | **Shareable / embeddable view** | A URL can be sent, bookmarked, embedded in the portal, or shown on a wallboard; a tmux pane cannot. | [#331](https://github.com/kushin77/agent-orchestrator/issues/331) |

## Cannibalize vs build

The repo already owns most of the hard parts. The web SPoG is an **assembly**
job, not a greenfield build — the rule is: cannibalize the projection and the
existing surfaces, build only the fleet-specific bindings.

| Piece | Cannibalize (use as-is or port) | Build (new) |
|---|---|---|
| Projection | `fleet/console.py` `snapshot()` — the single source of truth for what a frame contains | a thin adapter that serves `snapshot()` as JSON |
| Server | `portal/server/` (HTTP serving + routing), `control-plane/sdk/` (client contract) | `/api/fleet/snapshot`, the SSE/WebSocket push, `/api/fleet/events` |
| UI | `portal/static/` (app shell, asset pipeline), the leaderboard's dashboard patterns (live table, status badges, colour semantics) | the fleet frame component, filter bar, timeline, org roll-up |
| Layout idioms | public OSS terminal dashboards — Textual / rich — as *patterns* (widget composition, bounded refresh), never as runtime dependencies | the web widget set |
| AuthN/Z | `identity/rbac/`, `identity/cpapi/`, `identity/entitlements/` | the SPoG policy bindings (which rows a role may read) and the tenant-scoping middleware |
| Feature flags | `infra/feature-flags/` (GR-5: new surface ships flag-gated OFF) | the SPoG flag |

## The web SPoG roadmap (linked issues)

The work is deliberately split so each issue is one lane, one branch, one gate:

1. **[#331](https://github.com/kushin77/agent-orchestrator/issues/331) — projection server + streaming API**
   `fleet/console.py`'s `snapshot()` served as JSON plus a push channel and an
   events history endpoint. Cannibalizes `portal/server/` and `control-plane/sdk/`.
   Lane: `observability-finops`.
2. **[#332](https://github.com/kushin77/agent-orchestrator/issues/332) — browser dashboard**
   the live frame, filters, the historical timeline, and the multi-tenant org
   roll-up ([#151](https://github.com/kushin77/agent-orchestrator/issues/151)).
   Cannibalizes `portal/static/` and the leaderboard patterns. Lane: `portal`.
   Blocked by #331.
3. **[#333](https://github.com/kushin77/agent-orchestrator/issues/333) — access control**
   tenant scoping and role gating over the SPoG, consuming `identity/rbac`.
   Lane: `identity-rbac`. Blocked by #331.

Each issue brief carries its own acceptance criteria, Verify command, and
cannibalize-vs-build call, so the board — not this doc — is the working contract.

## The terminal TUI is not being removed

None of this retires `fleet/console.py`. The TUI stays the **local** operator's
instrument (zero network, zero dependencies, instant attach via
`python3 fleet/control.py live`), and the web SPoG becomes the **remote and
multi-tenant** surface. They share one projection so the two can never drift on
what "the fleet is doing" means; they differ only in who can look and what they
can query.

## Repointed — the single pane of glass is sourced from paperclip.ing, not built

EPIC [#359](https://github.com/kushin77/agent-orchestrator/issues/359) supersedes
the **build** conclusion of this document. The six capabilities named above are
still the requirement; what changes is **where they come from**.

### What this supersedes

The roadmap below — a from-scratch web single pane of glass split into a
projection server ([#331](https://github.com/kushin77/agent-orchestrator/issues/331)),
a bespoke browser dashboard
([#332](https://github.com/kushin77/agent-orchestrator/issues/332)), and
hand-written tenant scoping
([#333](https://github.com/kushin77/agent-orchestrator/issues/333)) — is the
plan this repoint replaces. That greenfield build is no longer the direction.

### What is no longer built

The bespoke web single-pane-of-glass UI as a **greenfield build**. The fleet
frame component, the filter bar, the historical timeline, and the multi-tenant
org roll-up are no longer written from scratch here. In their place the
agent-management surface is a fork/embed of **paperclip.ing** (MIT,
`github.com/paperclipai/paperclip`, self-hosted via
`npx paperclipai onboard --yes`), which already provides the org chart, goal
alignment, heartbeats, per-agent budgets with hard caps, tickets with full
tool-call tracing and an immutable audit log, and governance (approve hires /
pause / resume / override / terminate).

### What is still true and not affected

- **`fleet/console.py` is not removed.** It remains the local terminal
  operator's instrument, exactly as argued in "The terminal TUI is not being
  removed" above; the repoint changes the *remote* surface, not the local one.
- **The gap analysis still holds.** The six capabilities a TUI structurally
  cannot provide — multi-tenant org roll-up, historical timelines, filters and
  search, live streaming to a browser, access control / tenant scoping, and a
  shareable / embeddable view — are unchanged as requirements. They are now
  *sourced* from the paperclip.ing fork/embed rather than built here.
- **The projection is still `snapshot()`.** `fleet/console.py`'s `snapshot()`
  stays the single source of truth for what "the fleet is doing"; any paperclip
  surface that shows fleet state must consume it rather than re-derive it.

### Board items in scope for the repoint

- EPIC [#338](https://github.com/kushin77/agent-orchestrator/issues/338)
  ("Unified agent single pane of glass") and its children
  [#339](https://github.com/kushin77/agent-orchestrator/issues/339)–[#351](https://github.com/kushin77/agent-orchestrator/issues/351).
- [#331](https://github.com/kushin77/agent-orchestrator/issues/331) — projection
  server + streaming API.
- [#332](https://github.com/kushin77/agent-orchestrator/issues/332) — browser
  dashboard, filters, timelines, org roll-up.
- [#333](https://github.com/kushin77/agent-orchestrator/issues/333) — access
  control + tenant scoping.

### What this change does not do

This is a **recorded direction, not a takeover**. It does not close, relabel,
re-milestone, or reassign #338, its children #339–#351, or #331 / #332 / #333.
Ownership of each issue stays with its author, and each issue is re-scoped by its
owner in light of the repoint rather than executed as originally written. A
repoint records where the work is sourced from; it does not seize the work.

### Where the artifacts live

- `docs/PAPERCLIP-ING-GAP-ANALYSIS.md` — the paperclip.ing gap analysis
  (issue #368).
- ADR-0013 (`docs/decision-records/`) and `docs/PAPERCLIP-ING-INTEGRATION.md` —
  the integration seam (issue #370).

These paths are named here as plain text rather than links because they land on
`master` under issues #368 and #370, ahead of this document in the merge order;
links are added once the targets exist in the tree.
