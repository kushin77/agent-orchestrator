# Portal offline dev run — the fleet SPoG with zero external infra (issue #732)

> **DEV STOPGAP — this is NOT the production surface.** Everything below mints a
> throwaway session and promotes a surface **in a copy of the registry**, on
> loopback, for a reviewer who wants to *see* the fleet single-pane-of-glass
> today. It is not a deployment, not a rollout, and not a substitute for one:
> production serves this page from the shared-services run half and promotes the
> surface through `infra/rollout/` (`docs/AGENTCONSOLE-HOSTING.md`,
> [`OPERATOR-ACCESS.md`](OPERATOR-ACCESS.md) §4). Nothing here is committed, no
> secret is created, and no real identity is used — the helper **refuses** all
> three (see [What this refuses](#what-the-helper-refuses)).

The console (the fleet SPoG) is a self-contained static app served offline by
`python3 -m portal.server.main`. It ships **flag-gated ON by default** (AO-GR-6) and it
**fails closed** on sessions, so a plain "start the server and open the page"
gives a page whose every API call answers `404` or `401` with no visible reason.
This doc is the four steps that get a **200 with real fleet data**, and the table
that makes any other answer diagnosable. A reviewer reproduces it from this page
alone; [`scripts/check-portal-offline-spog.sh`](../scripts/check-portal-offline-spog.sh)
proves the whole matrix headlessly.

## What you get

- `http://127.0.0.1:8787/views/fleet.html` — the page, rendering the fleet
  projection (the same one `fleet/console.py` renders on the terminal);
- `GET /api/fleet/snapshot`, `/api/fleet/events?limit=8`, `/api/fleet/stream`
  (SSE) — the reads the page makes, answering `200` **with data**:
  `{"ok": true, "status": 200, "requestId": "req_…", "data": {"repo":
  "kushin77/agent-orchestrator", "head": "…", "rungs": {"brain": {…},
  "monitor": {…}, "sister": {…}}, "orders": …, "dispatches": …, "claims": […],
  "waves": […], "closed": […], "events": […], "watchdog": […]}}`.

## Prerequisites

- A full checkout of `kushin77/agent-orchestrator` (console + `fleet/` + `identity/`).
- **`python3` with `PyYAML` and `cryptography`** — the console's only runtime deps
  (there is no requirements file in this repo). `cryptography` is **required**:
  without it the server exits 1 at boot. Check with:
  `python3 -c 'import yaml, cryptography; print("ok")'`
- `curl`. No network, no container, no GCP project, no auth-gate deployment.

## Step 1 — sync the checkout to `origin/master`

The local checkout has drifted behind `origin/master` before, and a stale tree is
the one failure mode this doc cannot diagnose for you (a missing route or a fixed
bug looks exactly like a mistyped command). Sync first, and note the commit:

```bash
cd /path/to/agent-orchestrator
git fetch origin master
git status --short          # expect: empty. A dirty tree is yours to resolve.
git switch --detach origin/master   # older git: git checkout --detach origin/master
git log --oneline -1
```

## Step 2 — mint the offline dev session

One command mints the two things the console needs — the **JWKS mirror** it
verifies sessions against, and a matching **`os-session-token`** — plus a
**promoted copy** of the surface registry, and prints the exact `export`/`curl`
lines used below. Run it from the repo root:

```bash
python3 scripts/portal-dev-session.py
```

Real output (the session token is printed in full; elided here):

```text
== portal offline dev session — DEV STOPGAP, NOT the production surface (#732) ==
runtime dir   /run/user/1000/ao-portal-dev-session
identity      root@platform.example.com  (ROOT_ADMIN_EMAILS; the only allowlisted session)
tenant        platform   ttl 3600s
registry      infra/feature-flags/registry.yaml -> /run/user/1000/ao-portal-dev-session/registry-promoted.yaml
              surfaces.fleet_projection.default: on
jwks mirror   /run/user/1000/ao-portal-dev-session/auth-gate-jwks.json  kid=0gmQPLLHcIWY9TAeZVYxUlbdS-09LCL_9DIQUJcHtv0
validator     OK  keys=1 kids=0gmQPLLHcIWY9TAeZVYxUlbdS-09LCL_9DIQUJcHtv0 sha256=02f01815fdc12c73  (infra/portal/auth_env.py check-jwks)
validator neg an empty key set is REFUSED (rc 1) — the check above can fail
cookie        os-session-token
private key   never written: held in memory for the mint, then dropped
```

The `kid=`/`sha256=` line is the **mirror validated by the repo's own validator**
(`python3 infra/portal/auth_env.py check-jwks --root .`, which imports the
console's own `trusted_keys_from_jwks` predicate), followed by the proof that
validator *can* fail: an empty key set must be `REFUSED` (rc 1). If either half
does not hold the helper exits 2 and prints why — a mirror that "validates"
against a check that cannot refuse anything would prove nothing.

Everything it writes lands in a **runtime dir outside the repo**
(`$XDG_RUNTIME_DIR/ao-portal-dev-session`, override with `--runtime-dir`); a
`--runtime-dir` inside the checkout is refused, so nothing minted here can ever be
committed. The private key is generated in memory and **never written**.

### What the helper refuses

It is a dev stopgap, so it refuses to be used as anything else:

| Refusal | Why |
|---|---|
| `--email` outside a reserved RFC 2606 domain (`example.com/.org/.net/.edu`, `.invalid`, `.test`, `localhost`) | a real address means a real identity: this helper mints dev sessions only. Default `root@platform.example.com` is the identity the offline seed directory binds to every tenant. |
| `PORTAL_AUTH_GATE_JWKS`, `PORTAL_AUTH_GATE_JWKS_FILE` or `ROOT_ADMIN_EMAILS` set in the ambient environment | those are the env vars the console is **deployed** with. It refuses rather than mint against a real deployment — or be silently *shadowed* by one. Unset them first (`env -u ROOT_ADMIN_EMAILS python3 scripts/portal-dev-session.py`). |
| `--runtime-dir` inside the repository | every file it mints is runtime state; it must not be a committed path. |

### The two env vars, and where they come from

The console establishes a session **only** from a verified auth-gate RS256
`os-session-token`; with no key set configured it refuses every session
(`ConsoleAuthError: no auth-gate JWKS is configured; refusing every session`).
Two variables, both supplied by the helper's output:

| Variable | What it must be |
|---|---|
| `PORTAL_AUTH_GATE_JWKS_FILE` | Path to the mirror of the auth gate's published JWKS (`GET /auth/.well-known/jwks.json`). The inline form `PORTAL_AUTH_GATE_JWKS` (**takes precedence** over the file) carries the same JSON as a value. |
| `ROOT_ADMIN_EMAILS` | Comma-separated **super-admin** allowlist. It decides `root_admin` — the role that sees the whole cross-org projection — and **never** the token's own `role` claim. The console is not allowlist-only: an identity absent from it is still a `user`, scoped to whatever the offline seed directory binds it to. The helper sets it to the identity it minted, so the session you test with is the super-admin. |

## Step 3 — start the console (from the repo root)

```bash
export PORTAL_AUTH_GATE_JWKS_FILE=/run/user/1000/ao-portal-dev-session/auth-gate-jwks.json
export ROOT_ADMIN_EMAILS=root@platform.example.com
export AO_SURFACE_REGISTRY=/run/user/1000/ao-portal-dev-session/registry-promoted.yaml
export AO_SURFACE_STATE=/run/user/1000/ao-portal-dev-session/surface-state.json
python3 -m portal.server.main --host 127.0.0.1 --port 8787
```

**The working-directory trap.** `python3 -m portal.server.main` resolves the
`portal` package against the **current directory**: from `/tmp` it dies with
`ModuleNotFoundError: No module named 'portal'`. That is a working-directory
error, not a broken console. Run it from the repo root, or use the wrapper, which
resolves the root from its own path so the command is the same from anywhere:
`bash <repo>/scripts/console.sh --port 8787` (equivalently `make console`).

**The two seams that decide whether the surface is on.** `AO_SURFACE_REGISTRY`
points the console at a registry *file*, and the repo knows it
(`portal/server/fleet.py`) but no doc named it before this one — the doc-invisible
seam you need to promote a surface locally. It is read **at boot**, so export it
before starting the server: changing it later requires a restart. The second
seam, `AO_SURFACE_STATE`, names the **rollback overlay**
(`portal/server/surface_state.py`, default `.rollout/surface-state.json`), which
is consulted *before* the registry: a surface named there reads `off` **even
while the registry still declares it on**. That is its purpose — a withdrawal
that needs no commit and survives a restart — but it is also the trap: a
`.rollout/surface-state.json` left behind in a checkout silently reverts your
promotion and you see `404 feature_disabled` on a registry that plainly says
`on`. Step 3 therefore points `AO_SURFACE_STATE` at a path in the runtime dir
that does not exist: an **absent** overlay means no rollback is engaged, and the
declaration stands. (A *present but unreadable* overlay is treated as a rollback
in force — the reader fails closed — so do not point it at a broken file.)

## Step 4 — open the page and read the API

In a second shell, present the same session the server will verify (the page does
this itself once a browser has the cookie; `curl` is the honest check):

```bash
COOKIE='os-session-token=<the token printed in step 2>'
curl -i  -H "Cookie: $COOKIE" http://127.0.0.1:8787/views/fleet.html
curl -sS -H "Cookie: $COOKIE" http://127.0.0.1:8787/api/fleet/snapshot | head -c 400
```

Then open `http://127.0.0.1:8787/views/fleet.html` in a browser and set the same
cookie for `127.0.0.1` (the console's cookie name is `os-session-token`; a
browser needs it as a cookie, not a header). Measured replies:

```text
$ curl -sS -H "Cookie: os-session-token=…" http://127.0.0.1:8787/api/fleet/snapshot | head -c 320
{"ok": true, "status": 200, "requestId": "req_6fe445001195", "data": {"repo": "kushin77/agent-orchestrator",
 "head": "69ae5af", "now": "2026-09-16T16:56:22Z", "uptime": "up ?", "rungs": {"brain": {"pid": 1142586,
 "state": "no-heartbeat", "commit": "-", "beat_age": null, "started_at": null}, "sister": {"pid": 1191409, …

$ curl -sS -H "Cookie: os-session-token=…" http://127.0.0.1:8787/api/fleet/snapshot | \
    python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(sorted(d)); print(sorted(d["rungs"]))'
['claims', 'closed', 'dispatches', 'events', 'head', 'now', 'orders', 'repo', 'rungs', 'uptime', 'watchdog', 'waves']
['brain', 'monitor', 'sister']

$ curl -sS -H "Cookie: os-session-token=…" http://127.0.0.1:8787/views/fleet.html | head -c 90
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8">
```

The `rungs` are the fleet's own rung names from the console's declaration, and
the page's JS then reads `/api/fleet/snapshot`, `/api/fleet/events?limit=8` and
the `/api/fleet/stream` push channel (SSE). **An open stream never ends**: a
`curl -N` against `/api/fleet/stream` exits `28` (timeout) by design, having
flushed `event: snapshot` frames — a curl error code there is the expected shape,
not a fault. A rung reading `no-heartbeat`/`down` is honest, not an error: it
means that rung is not running in your checkout.

## The status matrix (why a 404 or a 401 is diagnosable)

Every code below is a **different** answer with a different fix. Measured against
the real server (`bash scripts/check-portal-offline-spog.sh` asserts all of them):

| Request | Flag OFF (registry as committed) | Flag ON, no cookie | Flag ON, minted cookie |
|---|---|---|---|
| `GET /views/fleet.html` | **200** | **200** | **200** |
| `GET /api/fleet/snapshot` | **404** `feature_disabled` | **401** `unauthorized` | **200** + the projection |
| `GET /api/fleet/events?limit=8` | **404** `feature_disabled` | **401** `unauthorized` | **200** + a JSON list |
| `GET /api/fleet/stream` | **404** `feature_disabled` | **401** `unauthorized` | **200** + SSE frames |
| `GET /api/healthz` | **200** | **200** | **200** |

Reading it:

- **`/views/fleet.html` is `200` while the API is `404`.** The static view is
  served by the static layer and is *not* gated; only `/api/fleet/*` is. So a
  page that loads but shows nothing is exactly "the flag is off", not a broken
  page — read the API.
- **`404 feature_disabled` = the surface is unpromoted or rolled back.** The gate
  runs **before** AuthN on purpose, so an unpromoted surface is *invisible*
  rather than distinguishable by an authentication probe. Fix: the promoted
  registry (`AO_SURFACE_REGISTRY`) and no engaged rollback (`AO_SURFACE_STATE`).
- **`401 unauthorized` = the surface is on and the session is missing or not
  verifiable.** The gate passed, so this is auth: no cookie at all, an expired
  token, a token signed by a key whose `kid` the mirror does not carry (the check
  asserts this case with a token minted from a *foreign* keypair), or the wrong
  token `purpose`. Fix: step 2, and read the server's own `ConsoleAuthError` line
  on stderr.
- **`200` with an empty `data` would not be a success.** The gate asserts the
  console's own projection is in the body (named rungs, the snapshot's keys), so
  "it answered 200" is never mistaken for "the SPoG renders".

## This box: a published container port is NOT reachable from the host

Measured twice on this laptop, once with a container built for nothing else:

```text
$ docker run -d -p 127.0.0.1:31222:8000 python:3.12-slim python3 -m http.server 8000
$ curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:31222/   ->  000 (curl: (56) Connection reset by peer)
$ docker exec <that container> python3 -c 'urlopen("http://127.0.0.1:8000/")'  ->  200
```

The port **is** published (`docker ps` shows `127.0.0.1:31222->8000/tcp`) and the
server **is** up; only the host → published-port path is broken (no daemon NAT /
userland proxy on this box). So do **not** run the console in a container with
`-p` and expect to browse it here — run it directly, as in step 3, and pick a
**free loopback port** (never assume `8787` is free: the check picks an ephemeral
port from the kernel rather than failing on a busy one). A sibling fact on the
same box: a bridge container cannot resolve DNS, so an image build that touches
the network needs `--network=host`.

## What this does NOT prove

Stated so the stopgap is not mistaken for the delivery:

- **Not a deployment.** The surface is promoted in a *copy* of the registry under
  the runtime dir; the committed declaration is untouched, and nothing is applied.
- **Not the production auth path.** The mirror here is minted locally with a
  throwaway keypair. Production gets the mirror from the shared-frontend auth
  gate (delivered as declared in [`infra/portal/auth_env.py`](../infra/portal/auth_env.py)),
  and a real `os-session-token` from a real sign-in.
- **Not the rollout.** Promotion/withdrawal in production is the
  `infra/rollout/` engine with its audit chain; step 3's `AO_SURFACE_STATE` is the
  same *reader* the rollback anchor writes to, exercised by hand.
- **Not a readiness or health statement.** `/api/healthz` is liveness; the
  console's readiness rail is `/api/healthz/ready`.

## The headless proof

```bash
bash scripts/check-portal-offline-spog.sh
```

It starts the real console on a free loopback port in three configurations
(committed registry; promoted registry + minted session; promoted registry + an
**engaged rollback**), asserts the whole matrix above over the loopback socket,
asserts the served snapshot is the console's own projection, then kills each
process and asserts **zero survivors and closed ports**. Exit contract: `0` OK /
`1` NOT-OK / `2` CANNOT-ASSESS (never a silent pass). It is offline, needs no
network and no container, and cleans up its scratch dir. The third configuration
is the [`surface_state`](../portal/server/surface_state.py) trap provoked: a
surface the registry still declares `on` goes `404` from an overlay alone.

## Teardown

Stop the server with `Ctrl-C`, then remove the runtime dir the helper printed
(`rm -rf /run/user/1000/ao-portal-dev-session`). Nothing in the checkout changed:
verify with `git status --short` — the only files this procedure creates live in
the runtime dir, and the session expires on its own TTL (default 3600s).
