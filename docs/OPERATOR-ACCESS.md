# Principal access — every way into the fleet, and what each one needs

> **Status:** runbook (issue #763). Normative companion:
> [`../fleet/CONTRACT.md`](../fleet/CONTRACT.md) §7.1 declares the steering
> channel the **PRIMARY control plane**; the runbook
> [`../fleet/README.md`](../fleet/README.md) describes the mechanics. This page
> answers one question: **as the principal, which command reaches which surface —
> and from where.**

A principal with no shell on the box used to have no stated path at all. This
page names every surface, gives the exact command for each, and says plainly
which ones need a shell on the box and which do not. Nothing here is invented:
every command below is the one the code implements, and the limits section
records what is **not** reachable today.

## Which do I use?

| I want to… | Surface | Command | Needs |
|---|---|---|---|
| **order work** (the primary control plane) | A2A control channel | `python3 fleet/channel.py order --message <file-or-inline-json>` | shell on the box |
| read the director's replies | A2A control channel | `python3 fleet/channel.py brain-outbox` | shell on the box |
| see the whole fleet on one screen | live principal view | `make operator` | shell on the box **+ tmux** |
| read state, health, or debug one thing | override terminal (observe) | `python3 fleet/control.py status` / `health` / `debug` | shell on the box |
| steer a running fleet (pause, poke, halt, refresh) | override terminal (steer) | `python3 fleet/control.py <verb>` | shell on the box |
| start / stop the rungs | override terminal (lifecycle) | `python3 fleet/control.py start` / `stop` / `restart` | shell on the box |
| browse the fleet from a browser, from anywhere | browser console | `make console` | shell on the box to start it; a browser to use it |
| **get a shell on the box from anywhere** | remote route (SSH over the Cloudflare Tunnel) | `infra/cloudflare/ao-ssh-access.sh` then `ssh <user>@<the hostname>` | the route published (§6, flag-gated OFF) **and** an Access service token to make it headless |
| **control** the fleet from outside the box | — | **not reachable today** (see [limits](#what-an-operator-with-no-shell-on-the-box-can-and-cannot-do)) | — |

## 1. The A2A control channel — the PRIMARY control plane

The steering channel is the fleet's primary control plane, not a fallback:
`fleet/CONTRACT.md` §7.1 states it normatively, `fleet/directive.json` carries it
into every executor prompt, and
[`../scripts/check-fleet-contract.sh`](../scripts/check-fleet-contract.sh) — in
`make verify` — fails by name when the declaration is removed. The transport is
the file mailbox of `fleet/channel.py`; the chain is
`principal → director → dispatcher → executor`, and the transport refuses a skip.

The principal **orders the director** — it never addresses the dispatcher directly
(`order` is the only way in; `send` refuses a principal sender):

```bash
# 1. order work — the order is a directive the director turns into a dispatch
python3 fleet/channel.py order --message '{
  "type": "directive",
  "task": {"issue": 763, "lane": "fleet"},
  "body": "dispatch one executor for #763 and report the evidence"
}'

# --message takes inline JSON or a path to a JSON file:
cat > /tmp/order-763.json <<'JSON'
{"type": "directive", "task": {"issue": 763, "lane": "fleet"}, "body": "go"}
JSON
python3 fleet/channel.py order --message /tmp/order-763.json

# 2. read the director's replies (acks and refusals), newest last
python3 fleet/channel.py brain-outbox
python3 fleet/channel.py brain-outbox --limit 5

# 3. watch the director's own inbox — the oldest order still waiting for it
python3 fleet/channel.py brain-inbox --timeout-seconds 5    # exits 1 (IDLE) if none
```

A worked example of ordering work, end to end:

1. `order` writes the order into the director's inbox and prints its message id.
   The message is validated first: a bad tier/thinking, an unknown type, or a
   replayed `nonce` is **refused** before it moves.
2. The director (`bash fleet/brain.sh`) drains it with `brain-inbox`, signs a
   directive for the dispatcher, and reports what it did.
3. `brain-outbox` is where the principal reads that answer — including a refusal,
   which names the contract rule it enforced.

Two properties that matter when ordering work:

- **A directive is the only authorisation for work** (§7.1 rule 1). An order is
  how a principal starts work; a comment or a verbal hand-off is not.
- **Superseding is explicit.** A new directive supersedes an earlier one for the
  same issue by its `supersedes` field or id — never by silent overwrite, so the
  order of orders is recorded in artifacts rather than reconstructed.

## 2. The human-override terminal — `python3 fleet/control.py <verb>`

The override terminal is the principal's direct lever on the rungs. It has 18
verbs; `python3 fleet/control.py --help` lists them, and
`docs/REMOTE-CONTROL-GAP-ANALYSIS.md` §2.1 inventories each one's reach.

**Observe** (read-only — no state is changed):

| Verb | What you get |
|---|---|
| `status` | rungs, pause/stop flags, tracked runs |
| `health [--stale-minutes N]` | tri-state signal: 0 healthy / 1 degraded / 2 failing |
| `debug [--tail N]` | a full, non-destructive state dump |
| `watch` | idle-watch the audit stream (`.fleet/slog.jsonl`) |

**Steer** (change what a running fleet is doing):

| Verb | What it does |
|---|---|
| `poke` | ping the dispatcher; it acks (liveness, without stopping it) |
| `pause` / `resume` | hold the queue / release it (an in-flight run finishes) |
| `override --issue N` | force `#N` past a live claim |
| `refresh` | `git pull --ff-only` + board snapshot + `make verify` |
| `update` | `refresh` + rebuild the knowledge index |
| `halt` | stop the fleet |

**Lifecycle** (start/stop the rungs on this host):

| Verb | What it does |
|---|---|
| `start` | start whichever rungs are missing |
| `stop` | exit after the current run, cleanly |
| `kill` | terminate the run, release its claim, escalate |
| `restart` | re-exec the same code (`SIGTERM` then re-exec) |
| `cron <sub>` | install/status/run/respawn/disable/enable/uninstall the cron entry |
| `live` / `attach [--dry-run]` | ensure the rungs, then `tmux attach -t fleet` |

`live` and `attach` are the same verb by two names, and both need **a shell on
the box and `tmux`**: the session is a *view* over rungs that run detached, and
without `tmux` the verb reports that it cannot host the view (exit 1) instead of
pretending it did.

## 3. The live principal view — `make operator`

```bash
make operator              # report the surfaces, then start/attach the live view
bash fleet/run-fleet.sh    # the same thing, one line shorter
python3 fleet/control.py live --dry-run   # print the tmux commands; build nothing
```

`make operator` prints the surfaces above (so the way in is discoverable from the
command itself) and then delegates to `fleet/run-fleet.sh`, which is
`python3 fleet/control.py live` — the tmux layout has exactly one definition
(`control.live_layout()`), and this target does not reimplement it.

**It fails loudly.** If the box cannot host the view (no `tmux`), it exits
non-zero and names the reason, then points at the same content without tmux:

```bash
python3 fleet/console.py         # the self-refreshing single-pane dashboard
python3 fleet/console.py --once  # one frame, for a script or a log
```

`make operator` never prints a success it cannot evidence.

## 4. The browser console (remote-capable) — `make console`

```bash
make -C /path/to/agent-orchestrator console           # 127.0.0.1:8787 (the default)
bash /path/to/agent-orchestrator/scripts/console.sh   # same thing, no `cd`
```

`make console` starts the real server (`portal/server/main.py`, backed by
`portal/server/httpd.py::serve`). It prints the line it is serving:

```
console: serving on http://127.0.0.1:8787 (loopback by default; FAILS CLOSED with no JWKS mirror)
agent-orchestrator console listening on http://127.0.0.1:8787 (static root: ...)
```

### 4.1 The working-directory trap (this bit a principal)

`python3 -m portal.server.main` resolves the `portal` package against the
**current directory**, so it works only when the shell is already at the repo
root. Run it from `$HOME` and it fails — which reads like a missing module but is
not:

```console
$ cd ~ && python3 -m portal.server.main --host 127.0.0.1 --port 8787
/usr/bin/python3: Error while finding module specification for 'portal.server.main'
(ModuleNotFoundError: No module named 'portal')
```

Three ways in, in order of preference — none of them needs you to `cd` first:

```bash
make -C /path/to/agent-orchestrator console                      # preferred
bash /path/to/agent-orchestrator/scripts/console.sh              # the same, as a script
PYTHONPATH=/path/to/agent-orchestrator python3 -m portal.server.main --port 8787
```

`scripts/console.sh` resolves the repo root from its own path and `exec`s the
module there, so the command is identical from any working directory. The raw
`python3 -m` form is shown last on purpose: it is the real command, and it is the
one that needs `PYTHONPATH` (or a `cd`).

Two facts decide whether this surface is safe, and both are properties of the
server rather than of this document:

1. **It binds loopback by default** (`127.0.0.1:8787`). Reaching it from
   elsewhere therefore requires deliberately binding a reachable interface
   (`--host`), which is a decision a principal has to make, not a default.
2. **It has no login of its own.** It redirects an unauthenticated visitor to
   the shared auth gate and establishes a console session only from a verified
   auth-gate RS256 `os-session-token`. With no JWKS mirror configured it
   **trusts no key and refuses every session — it fails closed**.

The two environment variables that configure it:

```bash
PORTAL_AUTH_GATE_JWKS_FILE=/etc/ao/auth-gate-jwks.json \
ROOT_ADMIN_EMAILS=root@platform.example.com \
  make console CONSOLE_HOST=0.0.0.0
```

- `PORTAL_AUTH_GATE_JWKS_FILE` — a mounted mirror of the auth gate's published
  JWKS (`GET /auth/.well-known/jwks.json`). **Without it, no session is
  accepted.** (The inline `PORTAL_AUTH_GATE_JWKS` payload is the alternative; a
  malformed mirror raises at boot rather than degrading to an empty trust set.)
- `ROOT_ADMIN_EMAILS` — the comma-separated allowlist that decides super-admin;
  the token never decides it.

**The security requirement, plainly:** never expose this console on a public
interface without the auth gate in front of it, and never rely on the bind
address as a control. A tunnel or reverse proxy in front of the console is a
**new infrastructure surface**: it is declared in code, ships **flag-gated OFF**
(GR-5), and lands as a reviewed change — never a console click and never an
ad-hoc `terraform apply`.

## 5. What a principal with no shell on the box can and cannot do

Honest limits, measured (`docs/REMOTE-CONTROL-GAP-ANALYSIS.md` §2.4/§2.6):

- **Read: yes, from a browser — once the console is reachable and the auth gate
  is in front of it.** The console's fleet-relevant families are GET-only and
  its projection is `fleet/console.py`'s snapshot; with no shell on the box, the
  console has to be started by someone who has one.
- **Control: not today.** No verb is reachable over a network: the transport is
  the local file mailbox plus local signals (ADR-0011), the console refuses
  every fleet mutation on that path, and there is no caller identity on the
  control path to authenticate. Stated as a surface, not as a promise.
- **The declared graduation path** is the remote control API
  (`surfaces.remote_control`, RC-3 of EPIC #551, issue #554): the
  principal→fleet command channel on the existing console app
  (`POST /api/control/<family>/<action>`), which consumes RC-2's closed verb
  declaration and re-implements no control action. It ships **flag-gated OFF**
  and the flag is checked **before** authentication, so while it is off the
  whole family answers `404 feature_disabled` — an unpromoted surface is absent,
  never silently available. Check the registry for the current state:

  ```bash
  grep -n -A 3 'remote_control:' infra/feature-flags/registry.yaml
  make feature-flags
  ```

So: the fleet is **ordered** from a shell on the box (the primary control plane,
§1), **watched** from a shell (`make operator`, §3) or a browser (`make console`,
§4), and **steered** from the override terminal (§2). A remote principal's path is
the console — read-only until the remote control API is promoted.

§6 adds the *transport* that removes the first of those needs — a shell on the
box — without changing any of the above: it publishes the host's SSH through the
Cloudflare Tunnel, so the shell the other sections assume can itself be reached
from anywhere. It is a route to the *host*, not to the fleet console.

## 6. From outside the box — SSH over the Cloudflare Tunnel

Sections 1–5 all start from a shell on the host, or from a browser that can
reach a console somebody with a shell already started. This section is the
**transport** that removes that first need: the host's own sshd is published
through the **existing** Cloudflare Tunnel with **Cloudflare Access** in front
of it, so a principal reaches the host from anywhere without opening port 22 to
the internet.

```bash
ssh <user>@<the published hostname>
```

The route is declared by [`../infra/cloudflare/ao-ssh-access.sh`](../infra/cloudflare/ao-ssh-access.sh)
(issues #771, #785). It is one coherent, end-to-end flow — provision the tunnel,
publish the hostname, deploy the connector — of which the publish step is the
four steps below and only the first has interesting logic:

1. **Merge an `ssh://<origin>:<port>` rule into the tunnel's live ingress
   configuration.** The Cloudflare API has no "add one rule" call: the tunnel's
   whole `config.ingress` array is rewritten, so a blind replacement would
   delete every *other* hostname the tunnel serves — an outage that reports
   success. The merge is therefore a pure function,
   [`../infra/cloudflare/ingress.py`](../infra/cloudflare/ingress.py): the rule
   for the target hostname is updated in place, a new one lands immediately
   before the trailing catch-all, and every unrelated rule comes back untouched.
2. **Upsert the proxied CNAME** `<hostname>` → `<tunnel id>.cfargotunnel.com`
   (update the record that already carries the name, else create it).
3. **Ensure the Cloudflare Access self-hosted application** for the hostname,
   plus an allow-policy listing the principal emails.
4. **Verify** the rule is present in the live configuration, and that the name
   resolves (A/AAAA).

The ingress / WAF / DNS rules this route obeys are stated once in
[`CLOUDFLARE-POLICY.md`](CLOUDFLARE-POLICY.md); this section does not restate
them.

### Provision the tunnel and deploy the connector (`--provision`, `--connector`)

Until issue #785 the publish step above *assumed the tunnel already existed* and
deployed *no connector* — the tunnel creation and the dual-node `cloudflared`
connector lived in another repo. The route now owns the whole flow:

```bash
infra/cloudflare/ao-ssh-access.sh --provision            # find-or-create the tunnel
infra/cloudflare/ao-ssh-access.sh --connector            # deploy the connector
infra/cloudflare/ao-ssh-access.sh --provision --connector  # the full e2e
```

- **`--provision`** (stage 0/5) finds the tunnel by name and reuses it, or
  creates it (seeding the trailing catch-all) when it does not exist. The read
  is **fail-closed**: an unreadable tunnel list is a refusal, never "no tunnel
  exists", so a create is only ever authorised by an honest empty list.
- **`--connector`** (stage 5/5) deploys `cloudflared` on each connector host —
  `docker pull` + `docker rm -f` + `docker run --restart unless-stopped` — so
  re-running converges to one running connector. Without a connector the tunnel
  is a configuration that serves nothing; this is what actually joins it to the
  origin. The connector's tunnel token arrives from the environment only, and
  the deploy is a **principal act** like `--apply`.

The provision and connector logic lives in a pure module,
[`../infra/cloudflare/provision.py`](../infra/cloudflare/provision.py), so it is
unit-tested and mutation-proved the same way as the merge.

### The hostname is useless without the Access app

Step 3 is not a nicety. A tunnel hostname published without an Access
application is an **unauthenticated public door to sshd** — it is a route into
the host for anyone who learns the name. That is why the script always ensures
the application *and* its allow-policy, and why there is deliberately no flag to
skip step 3: a published hostname without Access is not a supported posture, it
is the thing this route exists to avoid.

### Headless needs a service token

The Access application gives you the **interactive** flow: a browser, a
one-time-PIN, a cached login. That is enough to be let in, and it is not enough
to be *headless* — a script, a CI job or an editor cannot answer a PIN prompt.
A Cloudflare Access **service token** (plus the client-side wrapper that presents
it) is what makes

```bash
ssh <user>@<the published hostname>
```

connect with no browser and no cached login. The token is a separate, deliberate
principal act on the same Access application.

### What it reads from the environment

| Variable | Meaning |
|---|---|
| `CF_ACCOUNT_ID` | the Cloudflare account that owns the tunnel |
| `CF_ZONE_ID` | the zone that holds the published hostname |
| `CF_TUNNEL_ID` | the tunnel that serves the hostname |
| `AO_SSH_HOSTNAME` | the public hostname to publish |
| `AO_SSH_ORIGIN_HOST` | the host (or address) the tunnel reaches sshd on |
| `AO_SSH_ORIGIN_PORT` | the sshd port (default `22`) |
| `AO_SSH_ACCESS_EMAILS` | comma-separated principal emails for the allow-policy |
| `AO_SSH_ACCESS_SESSION_DURATION` | the Access session lifetime (default `24h`) |
| `CF_API_TOKEN` | the API token, **or** the two Secret Manager variables below |
| `AO_CF_TOKEN_SECRET` / `AO_GCP_SECRET_PROJECT` | the GCP Secret Manager secret and project holding that token |
| `AO_CF_API_BASE` | the API base URL — a seam for offline dry runs, never needed live |
| `CF_TUNNEL_NAME` | with `--provision`: the tunnel to find-or-create (required instead of `CF_TUNNEL_ID`) |
| `AO_SSH_CONNECTOR_HOSTS` | with `--connector`: comma-separated connector host(s) |
| `AO_SSH_CONNECTOR_USER` | with `--connector`: the SSH user on those host(s) |
| `AO_SSH_CONNECTOR_TOKEN` | with `--connector --apply`: the tunnel token (env/Vault/GSM — see below) |
| `AO_SSH_CONNECTOR_KEY` | with `--connector --apply`: path to the SSH private key |

Every identifier comes from the environment and a missing one is **refused by
name**: there is no default to fall back on, because a default would silently
point the run at somebody else's estate. The tokens are read from the environment
or from a secret manager — never from a file, never from git (GR-6).

### One secret posture: environment-first, Vault or GSM upstream

There is exactly **one** secret posture across the route, and it is
environment-first with a documented manager on each side of the fleet:

- **the API token** (`CF_API_TOKEN`) is read from the environment, or from
  **GCP Secret Manager** (`AO_CF_TOKEN_SECRET` + `AO_GCP_SECRET_PROJECT`).
- **the connector tunnel token** (`AO_SSH_CONNECTOR_TOKEN`) is read from the
  environment only. The principal sources it upstream from whatever manager the
  estate uses: on the **shared-services** (Vault) side that is a Vault KV read
  (`vault kv get -field=value <path>`), and on this repo's side GCP Secret
  Manager — either way it lands in the environment variable, never in a file and
  never in git.

Because the route takes *all* of its identifiers and tokens from the environment,
shared-services can vendor this module unchanged: it only has to populate the
same environment variables from its own Vault loader instead of from GSM. The
module itself is indifferent to which manager supplied the values (GR-6).

### Dry run first, then apply

```bash
infra/cloudflare/ao-ssh-access.sh              # dry run (the default)
infra/cloudflare/ao-ssh-access.sh --apply      # mutate
```

The dry run reads the live configuration and prints the **exact merged ingress
it would send** — every rule, in order, including the ones it is not touching —
so its output is evidence rather than a promise. It sends no mutating request at
all.

`--apply` refuses while the route's feature flag is OFF
(`surfaces.remote_ssh_access` in
[`../infra/feature-flags/registry.yaml`](../infra/feature-flags/registry.yaml),
OFF as delivered — check it before you start, and note that promotion is a
reviewed change, not an environment variable). The apply itself is a **principal
act**: it changes an estate this repo does not own, which is exactly the kind of
change an agent must not make.

### What this does not do

It reaches the **host**, not the fleet console. The console still refuses every
session until the auth-gate JWKS mirror is configured (issues #763 / #730), so
this section is the transport half of the principal-access gap and not the whole
path: once you have the shell, sections 1–3 are what you run in it.

The route is proved offline — no estate, no credentials, no Cloudflare call — by
[`../scripts/check-ao-ssh-access.sh`](../scripts/check-ao-ssh-access.sh).

## See also

- [`../fleet/CONTRACT.md`](../fleet/CONTRACT.md) §7.1 — A2A is the PRIMARY
  control plane (normative; the invariants).
- [`../fleet/README.md`](../fleet/README.md) — the runbook: bootstrap, mailbox
  layout, listener loop, day-to-day commands.
- [`REMOTE-CONTROL-GAP-ANALYSIS.md`](REMOTE-CONTROL-GAP-ANALYSIS.md) — the
  measured inventory of every control surface, and the remote-control EPIC.
- [`../docs/decision-records/ADR-0011-session-fleet-transport.md`](decision-records/ADR-0011-session-fleet-transport.md)
  — why the transport is what it is.
- [`../infra/cloudflare/ao-ssh-access.sh`](../infra/cloudflare/ao-ssh-access.sh) —
  the remote route itself (§6): the four steps, dry-run by default, flag-gated OFF.
- [`../scripts/check-ao-ssh-access.sh`](../scripts/check-ao-ssh-access.sh) — the
  gate that proves the route: a dry run mutates nothing, the merge never drops a
  live rule (mutation-proved), and no estate identifier is in the tree.
