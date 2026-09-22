# The chat mount contract — how the conversational surface appears in the OS shell

**Status:** decided (issue #511, EPIC #500). **Lane:** this document only — it decides and records the
mount; it writes no product code.

The EPIC's option E recommended two things that conflict with the owner's intent ("the OpenWebUI
experience *inside* our frontend portal"): swap the client to our gateway, and mount it as
`mount.type: tab`. A `tab` leaves the Single Pane of Glass entirely. This document resolves that
tension, records the rejected mounts with their reasons, and owns the cross-repo direction set that
makes the chosen mount buildable.

## The decision

**Mount our own portal chat view as a framed module:** `mount.type: iframe`, framing our own
same-origin `portal/static/views/chat.html`.

It is *our* document, served by *our* server, on *our* origin — so it inherits the single OS session
rather than negotiating a second one, and it is the surface that can actually speak the shell's
bridge. OpenWebUI stays a **non-authoritative** client.

## Measured facts this rests on

| Fact | Evidence (read 2026-09-14) |
|---|---|
| `mount.type` ∈ `iframe \| tab \| native`; props `type`, `url`, `entry`, `devUrl`, `sandbox`, `ephemeral` | `shared-frontend/modules/schema.json` (clone `c20d52c`) |
| `iframe` = a framed document, **no shared JS runtime**; all talk is `os:*` postMessage | `shared-frontend/docs/BRIDGE.md` (ADR-0001) |
| The bridge envelope is `os-module:ready` / `os:session` / `os:theme` / `os:navigate`, with a `contrib/portal-snippet.js` drop-in | `shared-frontend` `shell/src/bridge.ts`; pinned in this repo at `registry/portal-surfaces.pinned.json` (feature `iframe-mount`, flag `bridge.os-envelope`, default `on`) |
| `tab` opens a **new browser tab** — it leaves the SPoG | `shared-frontend/modules/schema.json`, `mount.type` description |
| The only `tab` module is `dialplane`, and it is `planned` — **not** a live precedent. `diagrams` is `native`/`live`, so the EPIC's cited `tab` precedent was wrong | `shared-frontend/registry/modules.json` |
| Auth is ONE Google login: HttpOnly `SameSite=Lax Secure` JWT cookie + `/auth/me` | `shared-frontend/docs/AUTH.md` |
| Our console **has no login of its own**; the portal verifies the shell's RS256 `os-session-token` offline against the auth-gate JWKS and **fails closed** | `portal/server/sso.py` module docstring ("This module is the module half of that contract") |
| The chat view is ours and same-origin | `portal/static/views/chat.html`, `portal/static/js/chat.js`, `portal/server/chat.py` |
| Per AO-GR-6 the flag defaults **on**; the surface is invisible only until **promoted**: while unpromoted the routes answer **404 before AuthN** | `infra/feature-flags/registry.yaml` → `surfaces.chat` (`default: on`, `promoted: false`, `tf_flag: enable_chat`) |

## The origin and session model

- **Same origin, one cookie.** The framed document is served by `portal/server` on the console's own
  origin, so the browser's existing HttpOnly session cookie applies. There is **no** second login, no
  token in a URL, and no trusted-header hand-off.
- **The shell hands over the session.** The auth gate mints the RS256 `os-session-token`
  (`purpose: os-session-token`, JWKS at `/auth/.well-known/jwks.json`); the shell passes it to a
  framed module over `os:session`. The portal **verifies** it offline against a configured mirror of
  the JWKS and fails closed on every error. It mints nothing and re-implements no JOSE
  (`portal/server/sso.py`).
- **RBAC is never taken from the token.** The local allowlist decides super-admin; a scoped user's
  roles come from the org directory, so a token cannot promote itself (`portal/server/sso.py`).
- **Consumed read-only:** `identity.sso.tokens.verify_console_session_token`,
  `identity.sso.tokens.allowlist_decision`.
- This model is already being converged by **EPIC #271** (one front door) — children **#272** (the
  portal consumes the OS auth-gate session and deletes its private login form) and **#273** (serve
  `ai.purebliss.app` through the auth gate + shell). This document does not re-open that decision; it
  states the mount that consumes it.

## The `os:*` messages the surface participates in

| Message | Direction | This surface |
|---|---|---|
| `os-module:ready` | module → shell | **participates** — announces the framed module is live |
| `os:session` | shell → module | **participates** — the hand-off that carries the `os-session-token` |
| `os:theme` | shell → module | **participates** — the surface renders the shell's theme, so it must not invent its own |
| `os:navigate` | shell → module | **declares** — the surface must not hijack top-level navigation |
| `os:auth` | shell → module | **not claimed here** — identity arrives on `os:session`; this surface requests no separate auth exchange |

**Honest current state, measured:** this repo **implements no `os:*` bridge today** — a repo-wide
search for `os:session`/`os:theme`/`os:navigate`/`os:auth` finds only prose (`portal/server/sso.py`,
`registry/portal-surfaces.pinned.json`), no emitter and no listener. So the table above is the
**contract this surface must meet**, not a description of shipped behaviour. Naming that gap is the
point: the mount is decided, and the bridge is the work item that remains.

## The flag-before-AuthN rule

`surfaces.chat` (`infra/feature-flags/registry.yaml`) is `default: on` (AO-GR-6),
`promoted: false`, with its own `tf_flag: enable_chat`, deliberately independent of the gateway and
the portal (ADR-0023 §5). While it is **unpromoted**, **the routes answer 404 before AuthN** — an
unpromoted surface is not merely unauthorised, it is **invisible**. Consequences the mount must
respect:

1. The vendor module entry must not be added while the surface is unpromoted; an entry that resolves
   to a 404 is a broken mount in the SPoG.
2. The flag is checked **before** the auth gate, so probing the route cannot distinguish "off" from
   "absent" — that is intended, not a bug.
3. Promotion is its own reviewed unit: promoting chat neither implies nor requires promoting the
   gateway or the portal it is embedded in.

## Rejected mounts, with reasons

| Rejected | Why |
|---|---|
| **`mount.type: tab`** | It opens a **new browser tab** and leaves the Single Pane of Glass — the exact opposite of the owner's intent. Also, its only precedent (`dialplane`) is `planned`, not live; the EPIC's cited `tab` precedent (`diagrams`) is in fact `native`, so the recommendation rested on a mis-read of `registry/modules.json`. |
| **Framing OpenWebUI as `iframe`** | Reintroduces the **double-auth anti-pattern** the EPIC's own §1.1 lessons record as fixed: OpenWebUI fronts its own identity (proxy auth + `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` → Keycloak) against our auth-gate `os-session-token`. Worse, BRIDGE.md gives a framed module **no shared JS runtime**, and OpenWebUI **speaks no `os:*`** — so session hand-off is impossible, not merely awkward. |
| **`mount.type: native`** (a React addon recreated in-shell) | Not rejected on merit — it is the viable alternative **if** the shell should render chat itself. It is not chosen because it needs six wiring surfaces in `shared-frontend` (`shell/src/addons.ts`, the module entry, `registry/fixtures/*.json`, the mount-coverage baseline, the vendor-deploy compose, `docs/INVENTORY.md`) and reimplements the view in another language for no user-visible gain today. Recorded so the choice is revisitable. |

## The direction set (tracked to completion)

- **`kushin77/shared-frontend` — already filed and open:** https://github.com/kushin77/shared-frontend/issues/493
  — *"Direction: mount a gateway-authoritative streaming chat surface in the OS shell (module entry +
  auth/bridge)"*. It asks for a `registry/modules.json` entry for this surface carrying `mount` +
  `bridge`, and, only if `native` is chosen, the six addon wiring surfaces. **Nothing under
  `shared-frontend` is edited from this repo** (NG4 / `docs/CROSS-REPO-EXECUTION-BOUNDARY.md`).
  (A closely-related second direction, `kushin77/shared-frontend#494`, is for the board there to
  reconcile — recorded here, not adjudicated here.)
- **`kushin77/shared-services` — the client re-point:** https://github.com/kushin77/shared-services/issues/4190
  — *"Re-point the OpenWebUI OpenAI-compatible connection from the Ollama VIP to the
  agent-orchestrator gateway"*. The running OpenWebUI instance is connected to the **Ollama VIP**
  (`infra/modules/openwebui/README.md` → `ollama_base_url`), so its turns never pass through our
  gateway and it is a **second, unaudited path** to the models. Re-pointing it is the vendor's work;
  filed as direction, never as a `Blocked-by:` marker — board numbers collide across repos.

Both URLs are recorded here **as prose on purpose**. A `Blocked-by:` number is parsed within this
repo's chain only, so a foreign board's number would be read as one of ours.

## What this document does not do

- It writes **no product code** and adds **no gate**: `docs/CHAT-MOUNT.md` is the artifact.
- It does not re-open ADR-0023 (`surfaces.chat`) or the #271 one-front-door decision — it consumes
  both.
- It does not implement the `os:*` bridge, and does not pretend the bridge exists. The measured gap
  is stated above.
- It edits no file in another repo.

## Verify

```bash
test -f docs/CHAT-MOUNT.md
grep -q 'mount.type' docs/CHAT-MOUNT.md
bash scripts/check-docs.sh
make verify
```
