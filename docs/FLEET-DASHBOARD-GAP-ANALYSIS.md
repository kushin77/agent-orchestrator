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
