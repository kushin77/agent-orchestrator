# Integrations e2e review — Paperclip / Hermes / DeepSeek / Claude (epic #1268)

## Scope

End-to-end frontend/backend/middleware review of the four runtime
integrations for epic #1268 ("mechanical, not doctrinal — paperclip / hermes
/ Claude / DeepSeek coordination"). Facts only, gathered by three parallel
traces (backend, frontend, middleware) against `origin/master` (`459a1d3b`),
2026-09-21. Not a doctrinal review — #1268 explicitly scopes that out.

## Per-integration status

| Integration | Frontend | Backend | Middleware |
|---|---|---|---|
| Claude (Anthropic) | missing — not named anywhere in `portal/static/**` or `control-plane/cockpit,control/**` | wired — `gateway/providers/anthropic.py:59` full custom Messages-API adapter, real wire protocol, tested via `gateway/providers` suite (143 passed) | wired (tier-space only) — `governance/dispatch/tiered.py` + `tier-policy.json` routes `claude`/`deepseek` |
| DeepSeek | missing — same as Claude | wired — `gateway/providers/deepseek.py:16`, platform *default* provider across all tiers (`registry.py:101-118`), real OpenAI-compat wire protocol | wired (tier-space only) — same `tiered.py` router as Claude |
| Hermes | missing | stubbed — `gateway/providers/hermes.py:22` real adapter code exists but flag-gated OFF by default (`gateway/providers/flags.py` `hermes_enabled()` fail-closed), catalog module `status: "active"` (issue #1907: reconciled from stale `"retired"`; `gateway/catalog/modules/hermes/module.json:14`); standalone `integrations/hermes/client.py` is real, keyless, read-only HTTP but "never called at dispatch time" per its own docstring (ADR-0012) | wired (capability-space only) — `fleet/routing.py` + `routing.policy.json` `personas: [hermes, paperclip]`; **not reachable from `tiered.py`** |
| Paperclip | missing | wired but not deployed — `gateway/providers/paperclip.py:22` + `integrations/paperclip/client.py` real OpenAI-compat + custom HTTP path, but platform-level `enable_paperclip` defaults `false`, no Cloud Run resource deployed (confirmed by merged #1610) | wired (capability-space only) — same `fleet/routing.py`, **not reachable from `tiered.py`** |

> **Amended 2026-09-22:** the "flag-gated OFF by default" / `defaults false`
> language in the Hermes and Paperclip rows above (and the flag-OFF state
> referenced in the gap list below) reflects the pre-2026-09-21 default,
> reversed by policy-gr5-enabled-by-default (2026-09-21, AO-GR-6); see
> docs/GOLDEN-RULES.md#ao-gr-6--flag-gated-off-by-default.

## Gap list

1. **P0 — filed #1701** (`Parent: #1268`): `governance/dispatch/tiered.py`
   (tier-space: claude/deepseek only, `tier-policy.json` has no
   hermes/paperclip key) and `fleet/routing.py` (capability-space:
   hermes/paperclip only, `routing.policy.json` `personas` has no
   claude/deepseek key) are two non-composing routers. A task dispatched into
   one has no adapter into the other, so epic #1268's own acceptance
   condition — a task flowing Claude → DeepSeek → Hermes → Paperclip and back
   — cannot happen today. The delivery layer underneath (mailbox
   `fleet/channel.py`, dead-letter `fleet/runaway.py`, per-runtime
   control-verb authorization `control-plane/control/verbs.yaml`
   `allowed_runtimes` checked against `fleet/schema/message.schema.json`) is
   real, mechanical, and runtime-agnostic — it would carry a task through if
   handed one. Nothing hands it one.
   - Partially covered by existing **#1562** (open, `Parent: #1510`, "Hermes
     agent claims work through governance/dispatch") — the Hermes half only.
     #1701 is the epic-#1268-scoped tracking issue for the general
     router-unification gap (Hermes + Paperclip), and notes #1562 as a
     partial fix.
2. **P2 — no issue filed, environment-only**: `integrations/paperclip` test
   suite shows 7 failed / 57 errors alongside 391 passed
   (`python3 -m pytest integrations/paperclip -q`). All traced to a missing
   `vendor/CMR/catalog` submodule checkout in this worktree, not a code
   defect in the runtime path — not a product gap, no issue filed.
3. **Not a gap — already closed**: the "Paperclip integrated" claim
   mismatch (doc language vs. flag-OFF runtime reality) was the subject of
   merged PR #1610 (closes #1566); confirmed fixed, no recurrence found in
   frontend or backend code during this review.
4. **Not a gap — already closed**: six working backends with zero console UI
   (FinOps, Ops/SLO, Org Chart, Skill Studio, Task Board, fleet board) named
   in epic #1510 were tracked as #1520–#1523, all now CLOSED.
5. **Not a gap — deliberate design**: the prompt-intake hook (#1638, closed)
   took the documented non-routing branch (ADR-0034); `.claude/settings.json`
   declares the decision, gate `check-prompt-intake-declaration.sh` enforces
   it. Frontend not naming any runtime is consistent with the anti-oversell
   nav-gating pattern in `portal/static/js/console.js:100-104` (a surface is
   absent, not a dead link, until its backend is promoted) — under-exposure,
   not misleading UI copy.

## Roadmap to e2e complete (dependency order, file-disjoint lanes)

1. **#1701** (this review) — build the tier-space ↔ capability-space
   adapter between `governance/dispatch/tiered.py` and `fleet/routing.py`.
   Blocks everything below; nothing else matters until one dispatcher can
   reach all four runtimes.
2. **#1562** — wire Hermes into the dispatcher's claim/peer-check identity
   classification (`governance/dispatch/cli.py`). Can land in parallel with
   #1701 on disjoint files (`governance/dispatch/*` vs the new adapter
   module) but is only end-to-end-meaningful once #1701's adapter exists.
3. **#1560** — Hermes agent lifecycle decision (self-host vs. Nous cloud).
   Gates whether `hermes_enabled()` can ever flip on; independent lane.
4. Paperclip platform activation (`enable_paperclip` flip) is an explicit
   **NO-GO** per the recorded decision in `docs/PAPERCLIP-PROMOTION-DECISION.md`
   (issue #1515) — out of this roadmap; the adapter in #1701 should be built
   and tested against Paperclip's flag-OFF state, not wait on activation.
5. **#1564** — operator controls (assign/pause/reassign/escalate/kill) on the
   Sessions view, once #1701+#1562 make cross-runtime tasks visible to route.

## Evidence

Full file:line traces gathered by three parallel research passes (backend,
frontend, middleware) against this worktree's checkout of `origin/master`
(`459a1d3b`); test commands and counts quoted above were run directly.
