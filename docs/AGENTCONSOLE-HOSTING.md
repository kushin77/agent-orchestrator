# AgentConsole hosting — where the operator console runs, and who makes it run

> **Status: LIVE** — go-live 2026-09-17 (epic #607). The console runs as the
> declared **shared-services** compose service (`shared-services-agentconsole`,
> `0.0.0.0:18286`) and `ai.purebliss.app` is cut over to it by a merged
> Cloudflare tunnel ingress rule. Measured at the edge: `GET /api/healthz` →
> **200**, `GET /api/fleet/snapshot` → **401** `missing console session token`
> (the console's own contract — not the legacy `302`), `GET /console` → **302**
> → `/auth/login`. The GCP Cloud Run route stays the **RETIRED** route.
> The repeatable go-live recipe is
> [`AGENTCONSOLE-GOLIVE.md`](AGENTCONSOLE-GOLIVE.md); the mechanical gate that
> keeps this contract from regressing is `scripts/check-agentconsole-hosting.sh`
> (issue #1029). This page is the handoff between the two halves of the fleet's
> live-hosting contract: it declares the console as a shared-services compose
> service and names what the run half still owes. Companion
> pages: [`OPERATOR-ACCESS.md`](OPERATOR-ACCESS.md) (which command reaches which
> surface), [`../fleet/CONTRACT.md`](../fleet/CONTRACT.md) (the control-plane
> contract), [`../portal/README.md`](../portal/README.md) (the portal half) and
> [§11](#11-the-modulecatalog-packaging-issue-813) below (the module/catalog
> packaging).

## 1. The problem this page closes

AgentConsole — the browser operator terminal — is merged (#774, rename #797) and
its transport is unified (#785), but it had **no stated live host**. The host was
implied by an older, retired route: `infra/terraform/modules/web-surface` declares
a Cloud Run service for a public web surface, flag-gated OFF and inert.

That route is no longer the fleet's. The contract, recorded by owner directive
2026-09-04 in shared-frontend `docs/DEPLOYMENT.md`, is:

> Live hosting of the OS portal shell and the vendored modules runs **only on our
> remote cluster, wired in the `shared-services` module** … This repo is the
> **source + build + registry** half; `shared-services` is the **run** half. A
> Cloud Run deploy pipeline … has been **removed**: it implied a live host that is
> not the live host. **No GCP deploy config lives here.**

So the console's declaration-of-record is the compose overlay in this repo, and
its hosting is shared-services' to run.

## 2. The two-repo split

| Concern | Where |
|---|---|
| Console source (`portal/`), the image recipe (`portal/Dockerfile`), the surface flag (`infra/feature-flags/registry.yaml`) | **this repo** — source + build + registry |
| The declared service and its run configuration | **this repo** — [`../contrib/shared-services/agentconsole.compose.yml`](../contrib/shared-services/agentconsole.compose.yml) |
| Building/publishing the image, lifting the overlay, DNS + tunnel ingress, oauth wiring, host secrets | **`kushin77/shared-services`** — the run half |
| The rebuild/verify gate for the source half | this repo: `make verify` |

The overlay follows the pattern the OS shell already went live with
(`os-portal-shell` in shared-frontend
`contrib/shared-services/docker-compose.frontends.yml`, container
`shared-services-os` on `192.168.168.42:18280`). A direction issue on the
shared-services board carries the run-half work; this repo never edits that repo.

## 3. What the console actually is (measured)

Every statement below is what the code does today, not an intention:

| Fact | Where it is true |
|---|---|
| One link, `GET /console`; the view is `views/console.html` + `js/operator.js` | `portal/server/app.py` |
| Surface flag `surfaces.operator_terminal`, **default `off`, `promoted: false`**, service `portal` | `infra/feature-flags/registry.yaml` |
| The flag is checked **before** authentication — while it is off, `/console` and its assets answer `404 feature_disabled`, so an unpromoted surface is absent rather than merely unauthorised | `portal/server/app.py` |
| It invents no login: an unauthenticated visitor is redirected to the OS auth gate's `/auth/login`, and a session exists only from a verified RS256 `os-session-token` checked **offline** against a mirror of the gate's published JWKS | `portal/server/main.py`, `portal/server/sso.py` |
| Read half = the existing `GET /api/fleet/*` projection; write half = the existing `POST /api/control/*` verbs, each with its own capability check and the existing audit rail | `portal/server/fleet.py`, `portal/server/control_audit.py` |
| `GET /api/healthz` is public and checked before authentication | `portal/server/app.py` |
| The image is stdlib-only Python (PyYAML is the only third-party import), builds from the repo ROOT, and its CMD binds `0.0.0.0:8080` | `portal/Dockerfile` |

## 4. The compose handoff

[`../contrib/shared-services/agentconsole.compose.yml`](../contrib/shared-services/agentconsole.compose.yml)
declares one additive service, `agentconsole` (container
`shared-services-agentconsole`), on the existing external `shared-services-net`.

**Port.** The container listens on `8080`; the overlay publishes
`0.0.0.0:${AGENTCONSOLE_PORT:-18286}`. `18286` is chosen clear of the live
surfaces (OS shell `18280`, modules `18282`–`18285`, gws `18290`) and **must be
re-checked on the host before the first `up`** — a port is a host fact, not a
declaration.

**Environment.** Only variables the code reads are declared; nothing else is
added for appearance:

| Variable | Read by | Empty/unset behaviour |
|---|---|---|
| `PORTAL_AUTH_GATE_JWKS_FILE` | `portal/server/sso.py` | with neither JWKS variable set, the console starts and **refuses every session** |
| `PORTAL_AUTH_GATE_JWKS` | `portal/server/sso.py` | inline payload; it **wins** over the file variable when both are set |
| `ROOT_ADMIN_EMAILS` | `portal/server/sso.py` | empty allowlist — no baked-in super-admin |
| `AO_FLEET_DIR` | `fleet/runtime.py`, `portal/server/control_audit.py` | defaults to `<repo>/.fleet`, which the image does not contain |
| `AO_LEDGER_DIR` | `portal/server/control_audit.py` | defaults to `<repo>/.portal/control/ledger`, which the image does not contain |

A malformed JWKS mirror is a deliberate **boot** failure, not a silent empty trust
set (`load_auth_gate_jwks` raises, and `ConsoleSso` calls it eagerly). The four
outcomes below were **measured inside the built image** (issue #801), not
inferred — the difference matters, because the middle rows are what tells the run
half that it exported a mirror path before the mirror was in place:

| Configuration | Observed |
|---|---|
| neither JWKS variable set | starts, reports `healthy`, `/api/healthz` 200 — and refuses every session |
| `PORTAL_AUTH_GATE_JWKS_FILE` set to a path whose content is not JSON | **exits 1** at boot: `ConsoleAuthError … is not valid JSON` |
| `PORTAL_AUTH_GATE_JWKS_FILE` set to a path that does not exist | **exits 1** at boot: `ConsoleAuthError … cannot be read: [Errno 2]` |
| `PORTAL_AUTH_GATE_JWKS` set to valid JSON with no usable RSA key (`{"keys": []}`) | starts and stays `healthy`; a session is refused by name — `no auth-gate JWKS is configured; refusing every session` |

So a misconfigured mirror is loud (a crash-loop under `restart: unless-stopped`)
rather than a silent, degraded trust set — and a **green healthcheck does not
mean the console is usable**.

**The image.** `portal/Dockerfile` builds from the repo ROOT and installs
**PyYAML and cryptography**. That second dependency is a fix this lane had to
make, and the reason is worth keeping: the image as it stood built cleanly and
then **could not serve its own CMD** — it exited 1 with `RuntimeError: portal SSO
requires the merged identity/sso lane (issue #35); import failed`, because the
boot path reaches `identity/sso/saml.py`, whose X.509 helpers
`identity/sso/jose.py` defines *only* when `cryptography` imports. Nothing had
noticed, because the only thing that ever built this image was the retired Cloud
Run route: the recipe had never been run. Verify the dependency set after any
base-image bump:

```bash
docker run --rm --entrypoint python3 <image> -c \
  "import cryptography, yaml; print(cryptography.__version__, yaml.__version__)"
```

**Live state.** `.dockerignore` excludes `.fleet`, `.board`, `.portal` and
`.telemetry` from the image, so a container that mounts nothing draws an **empty
fleet**. The overlay therefore mounts the host checkout's state, with the
justification for each:

| Mount | Why | Mode |
|---|---|---|
| `<checkout>/.fleet` → `/var/lib/ao/fleet` | rungs, heartbeats, waves, watchdog log, the brain mailboxes the projection reads, and the control audit slog | read-write — an allowed steer appends its audit receipt to this rail |
| `<checkout>/.portal/control/ledger` → `/var/lib/ao/ledger` | the control ledger the steer half appends a receipt to per applied command | read-write — same reason |
| `<checkout>/.board` → `/app/.board` | the claims ledger and the board snapshot the projection's `dispatch/cli.py status` reads, with `cwd=/app` | read-only — the console claims nothing |
| the auth-gate JWKS mirror → `/etc/ao/auth-gate-jwks.json` | the public key set session verification needs | read-only |

**Healthcheck.** `GET /api/healthz`. The probe prefers `curl` and falls back to
`python3`, because the runtime base image `python:3.12-slim` ships **neither curl
nor wget** — measured in the built image (`command -v curl` there prints nothing,
reported as `NO_CURL_IN_IMAGE`). A bare `curl` probe would leave every container
permanently `unhealthy`, a signal that can never go green. The image was measured
running with this probe: `healthy`, with the `python3` branch doing the work and
the same probe returning **rc 1 against a closed port** (so the check can fail —
it is not a formality). **Limit, stated plainly:** a green healthcheck proves the
process answers; it does not prove a session can be established. A readiness
signal of the surface's own is lane #802's deliverable, not this one.

## 5. Secrets posture (GR-6)

No secret is written into any file in this repo. The console's configuration is
environment-only, sourced by the run half at `up` time:

- **Project** `purebliss-ghl`, **deployer service account**
  `dev-machine@purebliss-ghl` — the identity the run half already uses.
- **GSM secrets** the fleet documents for these surfaces: `os-google-client-id`,
  `os-google-client-secret`, `os-jwt-secret-key`, `os-jwt-rs256-private-key`,
  `os-jwt-rs256-public-key`, `cloudflare-account-id`, `cloudflare-api-token`,
  `purebliss-cf-tunnel-token`.
- **The one mounted artifact is not a secret.** The JWKS mirror is the auth
  gate's *published* public key set (`GET /auth/.well-known/jwks.json`) — public
  by construction, and it is not committed here. The console needs no Google
  client secret and no signing key of its own: it issues no credential, it only
  verifies one the gate minted.
- The overlay interpolates secrets from the run half's environment and never
  holds them: every `${...}` in it either has an empty default (so an unconfigured
  bring-up fails closed) or is a public path, port or image tag.

### 5b. The deploy route takes the same two variables from Secret Manager (#730)

The compose handoff above gets its two variables from the run half's shell. The
**deployed** surface cannot: a variable exported on a box is configuration nobody
can audit, and an unset one is a console that refuses every session. So the Cloud
Run service declared by `infra/terraform/modules/web-surface` is wired to Secret
Manager, and the wiring is **declared once** in `auth-env.json`, shipped inside
that module (`infra/terraform/modules/web-surface/auth-env.json`), and projected
into the service — the env names exist in one non-code place, and
`scripts/check-portal-auth-env.sh` refuses a name restated in the Terraform.

| Variable | Secret Manager secret | How it reaches the container |
|---|---|---|
| `PORTAL_AUTH_GATE_JWKS_FILE` | `portal-auth-gate-jwks` | **a mounted file** at `/etc/ao/auth-gate-jwks.json` — the console *opens* that variable, so a payload injected as its value makes the console exit 1 at boot (rows 2-3 of the table above) |
| `ROOT_ADMIN_EMAILS` | `portal-root-admin-emails` | an injected secret **version** (the allowlist is a value) |

Both references are `latest`: a key rollover publishes a new version of the
mirror and the next revision mounts it. The runtime identity is a dedicated
service account whose only grants are reads on those two secrets.

The path is deliberately the same one the compose route mounts
(`/etc/ao/auth-gate-jwks.json`), so an operator reads one path and not two. The
full procedure, the refusal classes and the reasons are in
[`../infra/portal/README.md`](../infra/portal/README.md); the mirror job that
fetches the gate's published key set, validates it with the console's own
predicate and publishes it **on stdin** is
[`../infra/portal/mirror-auth-gate-jwks.sh`](../infra/portal/mirror-auth-gate-jwks.sh).

**Ordered, because the container is not the pipeline's to invent:** create the
two secret containers and publish the mirror *before* the surface is promoted
(`gcloud secrets create … --replication-policy=automatic`, then
`mirror-auth-gate-jwks.sh … --apply`). A container with no version makes the
revision fail to start, and the gate refuses a mirror with no usable signing key
before it is published rather than after it has refused every session.

`make verify` runs that gate, and the gate provokes each of its refusals against
a mutated copy whose unmodified twin it accepts — including the mirror's publish
path, driven with a real generated key set and a stub `gcloud` whose recorded argv
must not contain the payload. The offline reproduction of the deploy's own
success criterion (a signed-in session reaching `/api/console/me` with **HTTP
200**, read from the mounted mirror) is
`portal/tests/test_auth_gate_secret_env.py`.

### 5c. The promote rung's Artifact Registry credential (issue #1329)

`infra/fleet/promote_portal.py` reads the portal image repository
(`us-central1-docker.pkg.dev/purebliss-ghl/ao-images/portal`) to pick the tag
it deploys. It accepts exactly one of two auth shapes, both declared paths/
commands rather than values (GR-6):

- `AO_FLEET_AR_READER_KEY_FILE` — a service-account JSON key file mounted
  read-only (`infra/fleet/secrets_contract.py`'s `ar-reader-key` mount); or
- `AO_FLEET_AR_ACCESS_TOKEN_CMD` — a command whose stdout is a bearer token.

**OWNER ACTION (do this once):** place ONE of the two on the shared-services
pair — either mount a reader-scoped service-account key at the path
`AO_FLEET_AR_READER_KEY_FILE` names (default
`${HOME}/.config/ao/ar-reader-key.json`, see `secrets_contract.py`), or export
`AO_FLEET_AR_ACCESS_TOKEN_CMD` to a command that prints a valid AR bearer
token. Neither is created by this repository's tooling — with neither set,
the rung refuses `ar-auth-missing` (CANNOT-ASSESS), escalates once via the
fleet channel, and takes no action.

## 6. Where the console sits relative to the other live surfaces

| Surface | What it is | Live host / port |
|---|---|---|
| `ai.purebliss.app` | oauth2-proxy front over Open WebUI | remote cluster `192.168.168.42` |
| `os.purebliss.app` | the OS portal shell (container `shared-services-os`), live since 2026-09-04 | `192.168.168.42:18280` |
| **AgentConsole** | this page's subject, behind the same SSO gate | proposed `192.168.168.42:18286` |

Two things follow, and both matter:

1. **The console is not a second front door.** It has no login, no session
   issuer and no JWKS of its own. It consumes the *same* auth gate as
   `os.purebliss.app` and verifies the *same* `os-session-token` offline. So the
   oauth wiring is not a new oauth client: it is the auth-gate JWKS mirror plus an
   allowlist, exactly as `portal/README.md` documents.
2. **The ingress hostname is the run half's decision.** The overlay ships no
   hostname and this page proposes none as fact: DNS, the Cloudflare tunnel
   ingress rule and (if the run half wants the extra hop) an Access policy are
   enabled in the shared-services run half, the way `os.purebliss.app` was.
   Nothing in this repo hardcodes a domain.

Because it is a top-level console behind SSO and never an iframe, the overlay
carries no `os.origin` (framing/CSP) label — that convention belongs to the framed
modules, and adding it here would declare a policy nothing applies.

## 7. Rollout, rollback and the flag posture

- The surface is **declared and OFF**: `surfaces.operator_terminal` is
  `default: off` / `promoted: false`, and the gate is checked before
  authentication. An unpromoted console is *absent*, not merely unauthorised.
- Promotion is therefore a reviewed act with two independent halves: flip the
  flag in this repo (and the two surfaces it composes, `fleet_projection` and
  `remote_control`), and have the run half lift the overlay.
- **Rollback**: flipping `operator_terminal` back to OFF removes the surface
  again; `docker compose … down agentconsole` removes the process. Both are
  safe because the console writes no fleet state — its only writes are audit
  receipts on rails the fleet already owns.
- The **declared rollback *anchor*** (a rollback rule that fires on a health
  failure and is audited, with a gate probe that provokes it) is lane #802's
  deliverable. This page does not claim it exists.

## 8. What the run half must do

Handed over by direction issue on the shared-services board (never by an edit to
that repo):

- [ ] Lift the overlay (recommended `infra/docker-compose.agentconsole.yml`).
- [x] Publish the image and set `AGENTCONSOLE_IMAGE` — **done by the promote
      rung** (issue #1329): `infra/fleet/promote_portal.py`, scheduled as the
      `ao-fleet-promote-portal` cron rung (`fleet/cron.py`), reads the newest
      Artifact Registry `portal:<sha>` tag reachable from `origin/master`,
      recreates `agentconsole` with `AGENTCONSOLE_IMAGE=<that ref>`, health-
      gates the result, rolls back and escalates once on failure, and records
      every cycle to `.fleet/deploys.jsonl` + an `ao/deploy` commit status.
      The overlay's `agentconsole` service no longer carries a `build:`
      fallback — `AGENTCONSOLE_IMAGE` is required, and a local build is the
      separate, opt-in `agentconsole-dev-build` service
      (`--profile dev-build`). ONE OWNER ACTION REMAINS: place the Artifact
      Registry read credential on the shared-services pair — see §5c.
      **Scheduled but disabled (issue #1341):** `config/fleet-jobs.json`
      declares the `promote-portal` rung with `enabled: false`; flipping it to
      `true` is the owner's one-line follow-up once the §5c credential is
      placed. The rung's eventual `--apply` needs a Docker client talking to
      the host daemon, which `infra/fleet/inventory.yaml`'s `state_rw` section
      records as a wiring gap: the shared cron image installs no Docker CLI
      yet (`infra/fleet/Dockerfile`, out of that lane's file scope), and
      `infra/fleet/docker-compose.agent-cron.yml` mounts `/var/run/docker.sock`
      read-only on the `agent-cron-rw` (`state-rw` profile) service only —
      never its dry-run-only sibling.
- [ ] Re-check the host port, then publish `${AGENTCONSOLE_PORT:-18286}`.
- [ ] Seed the state mounts from the host checkout's `.fleet`,
      `.portal/control/ledger` and `.board`, and make the two read-write ones
      writable by the image's non-root `ao` user — otherwise the steer half's
      audit append fails closed (the image creates the `ao` user in
      `portal/Dockerfile`; a mount the container cannot write is a silent
      audit gap).
- [ ] Fetch the auth-gate JWKS mirror to the mounted path and export
      `PORTAL_AUTH_GATE_JWKS_FILE` for it; export `ROOT_ADMIN_EMAILS`.
      (On the **deploy** route this is §5b's Secret Manager wiring instead:
      publish the mirror into `portal-auth-gate-jwks` and the allowlist into
      `portal-root-admin-emails` before flipping `enable_web`.)
- [ ] Wire DNS + the Cloudflare tunnel ingress rule to `192.168.168.42:<port>`.
- [ ] Confirm `/api/healthz` answers and that an unauthenticated visit to
      `/console` redirects to the gate rather than serving the console.

## 9. Verifying this declaration (offline)

```bash
# the overlay is valid YAML and interpolates with the documented defaults
python3 -c "import yaml,sys; yaml.safe_load(open('contrib/shared-services/agentconsole.compose.yml'))"
docker compose -f contrib/shared-services/agentconsole.compose.yml config -q   # needs docker + no host secrets

# the console still serves its own public health answer, with no mirror configured
python3 -m portal.server.main --port 8787 &
curl -fsS http://127.0.0.1:8787/api/healthz      # {"status":"ok","service":"portal-console"}

# the image the run half will build: it must boot, and its declared probe must
# report healthy (add --network=host only where the build network has no DNS)
docker build -t agent-orchestrator-console:local -f portal/Dockerfile .
docker run -d --name console-proof \
  --health-cmd 'python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen(\"http://127.0.0.1:8080/api/healthz\",timeout=3).status==200 else 1)"' \
  --health-interval 5s --health-retries 3 agent-orchestrator-console:local
docker inspect -f '{{.State.Health.Status}}' console-proof    # healthy

# the repo gate stays green (the source half's gate of record)
make verify
```

## 10. Limits — what this handoff does not do

- It does **not** deploy anything, create DNS, or touch the live cluster. It
  declares and hands over.
- It does **not** fetch or hold a secret; the JWKS mirror is the run half's to
  place, and the GSM-held secrets stay in GSM.
- It does **not** add a readiness/metric signal for the surface (lane #802) or a
  rollback anchor (lane #802). The module/catalog packaging (lane C) was **not**
  this lane's to add either — it is delivered by issue #813, see §11.
- The image recipe is now bootable, but the *published* image is still the run
  half's to build and tag: until it does, `AGENTCONSOLE_IMAGE` is unset and the
  overlay falls back to a local `agent-orchestrator-console:local` build.
- The image has no `gh` binary, and the projection's closed-issue column is the
  one section that needs the network; `fleet/console.py` degrades that column to
  dispatched/pending rather than blanking the frame. Every other section reads
  the fleet's own files. This is a known, bounded degradation, not a fix made
  here.
- `infra/terraform/modules/web-surface` still declares the retired Cloud Run
  route, flag-gated OFF and inert. Retiring or repurposing it is not this lane's
  call and this lane did not touch it.

## 11. The module/catalog packaging (issue #813)

The console is not only a handoff — it is a **declared feature of this repo's
module manifest**, so the fleet's catalog can resolve the surface by name. Every
artifact lives in this repo; none of them is an edit to another repo.

| Artifact | Where | What it declares |
|---|---|---|
| Feature `operator-terminal` | root [`module.json`](../module.json) (`cmr.module/v1`) | `default: off`, `flags: ["surfaces.operator_terminal", "enable_portal"]` — the surfaces-registry key that actually gates `GET /console`, plus the portal service's Terraform flag |
| The surface's own gate | [`../infra/feature-flags/registry.yaml`](../infra/feature-flags/registry.yaml) → `surfaces.operator_terminal` | `default: off`, `promoted: false`, service `portal`, `tf_flag: enable_portal`; checked **before** AuthN, so an unpromoted console is absent rather than merely unauthorised |
| Catalog registration request | `kushin77/CMR#1014` | a **request** (NG4) to refresh the hub's copy of this manifest, `catalog/modules/agent-orchestrator/module.json`, from the root manifest — the hub is never edited from this repo |

**Provenance (GR-10)** for the console — the shared-frontend shell/native-addon
pattern and the shared-services hosting pattern, *pattern not code* — is recorded
in [`CANNIBALIZATION.md`](CANNIBALIZATION.md) §17. The console's own adapt-origins
table is in [`../portal/README.md`](../portal/README.md) §Provenance
(cannibalization).

Offline checks:

```bash
python3 -m json.tool module.json    # the manifest is well-formed JSON
grep -n 'operator-terminal' module.json    # exactly one feature entry
grep -n -A 3 'operator_terminal' infra/feature-flags/registry.yaml
```

Schema conformance needs the hub's `catalog/schemas/module.schema.json`, which is
vendored at `vendor/CMR` once the submodule is initialised:

```bash
python3 -c "import json, jsonschema; \
  jsonschema.Draft7Validator(json.load(open('vendor/CMR/catalog/schemas/module.schema.json')))\
    .validate(json.load(open('module.json'))); print('SCHEMA_OK')"
```

When the submodule is not initialised the module gates (`module-registry`,
`module-brief`) report **CANNOT-ASSESS** rather than a pass — offline is not the
same as verified.
